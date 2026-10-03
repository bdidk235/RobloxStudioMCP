---
name: rsx-breakpoints
description: Breakpoints and logpoints via extended_breakpoints. Read before setting one: two settings that fail silently.
---

# Breakpoints

`extended_breakpoints` sets non-halting logpoints on a running server script.
A play session must be live, because `AddBreakpoint` targets `ServerScriptService`.

## If you wanted to *pause*, you want `rbx-debug`, not this

Read that first, because it is the most common wrong turn available here.

This tool is **logpoints only** — they print a line and keep going. Roblox's own
`rbx-debug` skill, fetched through the relayed `skill` tool, does the other half:
halting breakpoints, `OnStopped`, and thread inspection. That work cannot be
driven from a synchronous tool call here, because a non-continuing breakpoint
halts the very scheduler that would return the result (measured below: a 120
second MCP timeout, with the breakpoint working fine the whole time).

So: **you want to stop and look at state → `rbx-debug`. You want to know a line
is being reached and what it holds → this skill.**

For a value you want *once*, prefer reading it outright rather than trapping it.
`rbx-scene-analysis` is better than hand-rolled tree walks, and this project's
`extended_script_search_and_read` beats a breakpoint for reading a source file.

## There is no `action` parameter

**Read this before you send anything.** An earlier version of this skill said the
tool took `action: set | remove | clear | list`. It does not, and never has.
That text described the internal registry op set in `breakpoints.py`, which is
not the tool's surface — and an agent following it would send `action`, get
refused by name, and learn the interface from an error message.

The real surface is two tools, and `extended_breakpoints` **toggles** rather than
taking a verb:

| To do this | Call |
|---|---|
| set one, or remove it if already there | `extended_breakpoints(script_path=…, line=…)` — same call either way |
| clear all | `extended_clear_breakpoints()` |
| list | none. `list` is internal to the registry and is not exposed as a tool |

The tool asks the registry whether that exact `script_path` + `line` already has
a breakpoint, and sets or removes to match. That is deliberate: you say *where*,
not *what*, and there is no enum that can disagree with the actual state. The
cost is that the same call is not idempotent — **calling it twice in a row
leaves you with no breakpoint**, and it reports success both times. You do not
have to guess which happened: the result carries which, as **`added`** or
**`removed`** — never both. `removed` is just `{script_path, line}`;
`added` is the full set record, with `log_expression`, `verified` and
`continue_execution`. Read the key before calling again.

A breakpoint that set successfully and never appears in the console is the
failure this skill's next section is about.

## `ContinueExecution = true` is mandatory

A non-continuing breakpoint halts the whole script scheduler, which blocks the
very tool call that set it. Measured as a **120 second MCP timeout** with the
probe never resuming. The breakpoint works; the tool call that created it never
returns.

Pausing is genuinely useful, but it cannot be driven from a synchronous tool
call. If you need to pause, use the `OnStopped` path in Roblox's `rbx-debug`
skill.

## A `log_expression` that fails is what gets reported

`LogMessage` is a Luau expression evaluated on every hit. A **successful** log
injection is not visible through `get_console_output`; it goes to Studio's own
log sink. An expression that **errors** is surfaced instead, one line per hit,
already prefixed by the engine:

```
Breakpoint ServerScriptService.BpTest:5 ignored: [string "logExpression"]:1: HIT i=7
```

So the default is `error("hit")` and the deliberate failure *is* the reporting
channel. An expression that passes reports nothing at all, with no error, which
reads exactly like "that line never ran".

Nothing enforces this. An omitted or falsy `log_expression` is silently
replaced with `error("hit")`; a supplied one is sent through untouched, and a
passing one is accepted and returns normally. The failure is the reporting
channel by convention, not by validation.

Interpolate the locals you want:

```
error("i=" .. tostring(i) .. " state=" .. tostring(state))
```

Keep the expression self-contained. It runs in a sandbox that cannot see globals
defined by your own probe.

## The registry folder must be stable across calls

`ScriptDebuggerService` exposes no way to enumerate breakpoints, so the registry
is mirrored into a studio-only folder under `PluginGuiService`.

The mirror is the **only** record. `list` walks the folder's children and never
reads engine state, and the toggle decides set-or-remove by whether the folder
has an entry for that `script_path` + `line`. So a breakpoint set Studio-side
is invisible to the tool: the registry sees no entry, the next toggle at that
location **adds a second** breakpoint rather than removing the first, and `list`
reports only the ones this tool made.

That folder must be **stable**, created once and reused. An early version made a
fresh per-call folder, and the symptom was the internal registry op returning
empty while the breakpoint itself worked perfectly. A confusing failure that
looks like the debugger is broken when it is fine.

## Reading hits

Hits are ordinary console lines, so `extended_watch_output` carries them in the
same stream as everything else. Filter on the `Breakpoint ` prefix.

Match the pattern, do not parse lines — unconditionally. The console's newline
shape is consumer-dependent: on some consumers it arrives as one line with
literal `\n` escapes, and through the agent caller path it arrives with real
newlines, so `splitlines()` is right on one shape and silently wrong on the
other. Better to assume only that `extended_watch_output` splits lines for you,
returns `{lines, returned, matched, total_seen, truncated}` rather than a bare
string, and caps each poll at 200 lines with only the last 20 on the first
poll — so a single poll showing 20 lines with no hit proves nothing.

See `rsx-console` for the reasoning behind match-don't-parse, and for the
digit-scan failure that made 60 correct hits look like garbage.

## Verified behaviour

On a loop of 80 iterations with the breakpoint on the loop body:

- **60 contiguous hits, i=1 through i=60**, from the very first iteration
- the live local was interpolated into each line and read back correctly
- set -> hits -> toggle to remove -> clear all clean, registry verified emptied

## Attaching reliably

Two things will silently produce zero hits:

- the script finished its loop before the breakpoint attached. Give the script a
  `task.wait()` window at startup, and set the breakpoint inside it.
- calling `start_stop_play` on an already-running session resets it and
  detaches anything set moments earlier. Stop first, then start, then attach.

## For richer inspection, use `rbx-debug`

Logpoints force you to hand-build a string and parse it back, which gets
unpleasant past two or three values. Roblox's own `rbx-debug` skill documents
the better path: an `OnStopped` callback with `GetThreads`, `GetStackTrace`,
`GetRootVariables`, `GetVariables` and `Evaluate`, which gives structured
inspection while the DataModel is paused.

Two constraints from that skill, both easy to get wrong:

- `OnStopped` is **per DataModel** and does **not** propagate from the edit
  DataModel to the play DataModels. Install it on each play DM *after* the
  session starts.
- a callback that throws, or does not return a resume action, **resumes by
  default**. Always return an explicit `Enum.DebuggerResumeType`.
