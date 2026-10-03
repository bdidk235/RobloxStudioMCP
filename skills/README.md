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

## Why a skill and not a longer tool description

A tool description is paid on **every call of every session**. A skill is read
only when it is needed. Seven skills carry 55,465 characters of body against a
1,985-character index, so under 4% is always-on.

Those two figures measure different things, so name the measure when you quote
either. "Body" is `content`: the file with its frontmatter block stripped and
the ends trimmed, which is what `skills.py` sends to a model. Raw bytes on disk
come to 56,779, frontmatter included. Both drift as skills are added, so
re-measure instead of trusting this line:

```
# characters the loader counts as body, and the index it builds
python -c "import sys;sys.path.insert(0,'python/src');from roblox_studio_mcp.extended.skills import load_skills,skill_index;s=load_skills();print('bodies',sum(len(x['content']) for x in s),'index',len(skill_index(s)))"

# raw bytes on disk, frontmatter included
Get-ChildItem skills\rsx-*.md | Measure-Object -Property Length -Sum
```

No test asserts those totals, because they move for uninteresting
reasons. `test_skills.py` asserts the ratio, which is the claim that matters: the index must
stay under a quarter of the bodies.

## The description budget

Tool descriptions are capped per tool and in total, and both caps are enforced
by tests in each implementation. The figures live in the generated contract,
`contract/tools.json`: read `per_tool_description_cap`, `total_description_cap`
and `total_description_chars` there rather than here. That file is produced by
`contract/build_contract.py`, so a regeneration is also the diff that explains any
change.

The total was 2,700 until 2026-10-01, when it was raised to 3,200 by decision.
The old cap forced a choice between saying what an action returns and staying in
budget, which was the wrong trade. An agent that does not know a launch hands
back a `studio_id` makes a second call to find one, so the cap was dictating
tool behaviour. What the cap is for is prose that is not what, when or why, and
that is caught by reading each description, not by starving them.

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
checked rather than trusted: both suites fail a skill that breaks either.

## Adding one

1. Create `skills/rsx-<name>.md` with frontmatter whose `name` matches the
   filename.
2. Run the Python tests (`python -m pytest tests -q` from `python/`).
   They parse the real files, so a malformed one fails the suite.
3. The index is rebuilt from the files, so there is nothing to register.

## Sharing between implementations

The skills live once, here at the repository root, and the Python
loader finds them by walking up from its own source file. There is no second
copy to keep in sync.
