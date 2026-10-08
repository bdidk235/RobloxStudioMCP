# Skills

Markdown documents served on demand by the `extended_skill` tool. Call it with
no `skill_name` for the index, or with a name to fetch one.

## What belongs here

**Transport traps only.** These skills exist for the things that go wrong in
*this* MCP and nowhere else. If a fact is about the Roblox engine, Roblox
already documents it better through the relayed `skill` tool, so duplicating it
here is waste.

| skill | covers |
|---|---|
| `rsx-transport` | return truncation, scratch-module ceiling, dot-free names, base64 |
| `rsx-console` | the one-line console, never printing bulk data, attributes as the reporting channel |
| `rsx-targeting` | `studio_id` vs `datamodel_type`, no durable identity, getting a PID |
| `rsx-breakpoints` | logpoint mechanics, and when to defer to `rbx-debug` |
| `rsx-playtest` | play and multiplayer test flow, and the wedge to avoid |
| `rsx-capture` | `screen_capture` vs `extended_capture` |
| `rsx-discovery` | reflection gotchas, and which services to reach for |

The `rsx-` prefix is load-bearing. Roblox's skills are `rbx-*`, and keeping the
two apart is what stops an engine fact and a transport fact being confused.

## The other family, and how to route

**These are two separate skill systems reached by two different tools.**
`extended_skill` serves the table above. The relayed `skill` tool serves
Roblox's, and those are the ones to consult for anything about the engine.
Neither tool serves the other's names: asking `extended_skill` for an `rbx-*`
name, or the relayed `skill` for an `rsx-*` name, gets you a near-miss
suggestion at best, not the document.

**No plugin is involved in either.** `extended_*` is not a Studio plugin — it is
this project's client in front of the MCP Studio already ships and already has
open, and the `rbx-*` skills come from that same built-in assistant. Nothing
here is installed into a place, and nothing is fetched from a cloud service.
That is why the `rbx-*` set cannot be configured or extended from our side: we
relay it.

Decide which wire the question is on before choosing:

| your question | go to |
|---|---|
| How do I get this value out of Studio over this connection? | `rsx-*` |
| Does this engine API exist, and what exactly is it called? | `rbx-docs-search` |

Roblox's set, and when each is the right call:

| skill | use it for |
|---|---|
| `rbx-debug` | **pausing** and thread inspection — the answer whenever you want to stop and look |
| `rbx-docs-search` | engine API reference, creator guides, CLI flags such as `--task` and `--localPlaceFile` |
| `rbx-scene-analysis` | instance composition, parenting, animation, adornments — better than a hand-rolled tree walk |
| `rbx-unit-test` | TestEZ authoring, and why a test failed |
| `rbx-perf-profiling` | MicroProfiler; we have no equivalent |
| `rbx-device-simulator-lua` | UI across device form factors |
| `rbx-create-skill` | authoring a new `rbx-*` skill inside Studio |

**The overlap is deliberate and it is not a conflict.** Split by half of the
question. `extended_run_tests` runs the engine's test runner but cannot tell you
why a test failed; `rbx-unit-test` can, but knows nothing that a console line
arrives as one line. Take the engine half from `rbx-*` and the transport half
from `rsx-*`.

The most common wrong turn is reaching for `extended_breakpoints` when you want
to *pause*. `rsx-breakpoints` is non-halting logpoints only, because a halting
breakpoint blocks the very tool call that sets it. For pausing, it is
`rbx-debug`.

A skill can also document something you cannot reach from where you are.
`rbx-debug` prescribes `OnStopped`/`GetVariables`, but `OnStopped` is per
DataModel and does **not** propagate from edit to play (`rsx-breakpoints`
records both constraints) — so the recipe works and the default DataModel
does not. Read the reachability alongside the recipe. And read before driving
in general (`AGENTS.md`: read the skill before touching the transport): the
trap sections exist because the failure they describe has already happened.

Identity state lives host-side, never in the place: the registry this project
keeps is in its own state directory, and a `studio_id` names a mesh row, not
anything stored in the game.

This file itself is **not** served. `skills.py` ignores `README.md`
(`SKILL_IGNORED`), so `extended_skill` serves the skills, never this index
document — treat a fetched skill as the entry point, not this page. It *is*
packaged, because it shares the folder's `*.md` glob, so the routing tables
above travel with the skills they route between.

## Why a skill and not a longer tool description

A tool description is paid on **every call of every session**. A skill is read
only when it is needed. The index stays small enough that always-on cost is a
rounding error next to the descriptions — and that ratio is the claim worth
making, because it is the one a test pins.

The two quantities measure different things, so name the measure when you quote
either. "Body" is `content`: the file with its frontmatter block stripped and
the ends trimmed, which is what `skills.py` sends to a model. Raw bytes on disk
are larger, frontmatter included.

**Neither total is written here, and that is deliberate.** Both went stale the
moment a skill was edited, and a figure that only *might* be right is worse than
none. `TODO.md` records that drift and the correction, dated. Re-measure with:

```
# characters the loader counts as body, and the index it builds
python -c "import sys;sys.path.insert(0,'python/src');from roblox_studio_mcp.extended.skills import load_skills,skill_index;s=load_skills();print('bodies',sum(len(x['content']) for x in s),'index',len(skill_index(s)))"
```

No test asserts those totals, because they move for uninteresting
reasons. `test_skills.py` asserts the ratio, which is the claim that matters: the index must
stay under a quarter of the bodies.

## The description budget

Tool descriptions are capped per tool and in total, and both caps are enforced
by the suite. The figures live in the generated contract, `contract/tools.json`:
read `per_tool_description_cap`, `total_description_cap` and
`total_description_chars` there rather than here. That file is produced by
`contract/build_contract.py`, which also carries why the total sits where it
does, so a regeneration is the diff that explains any change.

**The cap is not to be raised again.** New surface is funded by trimming
existing descriptions. Depth still belongs in a skill rather than a description,
because a description is paid every call and a skill is not.

## Format

One markdown file per skill, with a small frontmatter block:

```markdown
---
name: rsx-example
description: One line naming the trigger. Kept under 160 characters.
---

# rsx-example

The body, read only on demand.
```

The loader is strict on purpose. Frontmatter is required, `name` and
`description` must both be present, and `name` must match the filename. A
malformed skill raises rather than being skipped, because a silently dropped
skill is a library that quietly shrank and nobody notices.

The 160-character limit on `description`, and the rule that it is one line, are
checked rather than trusted: the suite fails a skill that breaks either.

## Adding one

1. Create `rsx-<name>.md` in this folder, with frontmatter whose `name` matches
   the filename.
2. Run the Python tests (`python -m pytest tests -q` from `python/`).
   They parse the real files, so a malformed one fails the suite, and
   `test_skill_refs.py` checks every `file:line` pointer in the body.
3. The index is rebuilt from the files, so there is nothing to register.
4. Nothing to add to the packaging: `[tool.setuptools.package-data]` already
   globs `skills/*.md`, and a test fails if a file here is not covered by that
   glob.

## Where these live, and why inside the package

This folder **is** the shipped copy: `python/src/roblox_studio_mcp/skills/`,
inside the package, declared in `pyproject.toml` as

```toml
[tool.setuptools.package-data]
roblox_studio_mcp = ["py.typed", "skills/*.md"]
```

so one copy is what lands in the wheel and what an install loads. The loader
resolves it beside itself - `find_skills_dir()` checks
`roblox_studio_mcp/skills` first and only walks up as a source-tree fallback -
and raises `SkillError` when neither exists, because an empty catalogue reads
exactly like a working one.

**The layout used to be the wrong way round, and it failed silently.** The
skills sat at the repository root, outside the package, with a loader that
walked up from its own source file looking for them. That works in a checkout
and nowhere else: the wheel is built from the package, so it contained 24
modules and none of these 8 files. Measured in a clean venv on the wheel built
before the move:

```
find_skills_dir() -> None
skills loaded: 0
skill names: []
```

No error, no warning - a successful answer with nothing in it. Two things were
wrong there and both had to be fixed: the data was not packaged, and the loader
answered `[]` instead of complaining. `test_skills.py` now builds a real wheel
and reads its listing, and imports the package alone from outside the repository,
so a repeat of either half fails the suite rather than shipping.

So the argument that used to close this section - "the skills live once, at the
repository root, and there is no second copy to keep in sync" - survives, but
only because the location moved. One copy, one place, and it is the place that
ships. What does **not** survive is the claim that a walk-up could ship it: it
cannot, and the measured listing above is the proof.

