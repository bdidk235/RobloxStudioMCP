---
name: rsx-console
description: Never parse console output by line - its newline shape is consumer-dependent, so a per-line parser is silently wrong on one shape. Read before parsing it.
---

# Reading Studio output

## `print` is the shape-preserving route. `return` is not. Read this first.

**The intuitive choice is the wrong one.** To get a value out of Studio you reach
for `return`, because that is what a function is for. On this wire, `return`
**loses structure silently** and `print` does not.

Measured live, 2026-09-30, same Studio, same call:

| what the Luau did | what arrived |
|---|---|
| `return {10,20,30}` | `{"1":10,"2":20,"3":30}` — an **object**, not `[10,20,30]` |
| `return {rows={{n=1,v=10},{n=2,v=20}}}` | `{"rows":{"1":{…},"2":{…}}}` |
| `return {p=Vector2.new(3,4)}` | `{"p":"3, 4"}` — a **string**, not `{x,y}` |
| `print(HttpService:JSONEncode({10,20,30}))` | `[10,20,30]` — **intact** |

Note the shape of the bug: the third row is worse than "keys got stringified".
`Vector2` arrives as the *single scalar string* `"3, 4"`, so the field names are
gone **and** the structure is gone. Recovering it means parsing a string, which is
guessing.

**So: to return data, serialise at the source.**

```lua
local HttpService = game:GetService("HttpService")
print(HttpService:JSONEncode({ rows = { {n=1,v=10}, {n=2,v=20} } }))
```

The JSON travels as a **string**, and a string is not reinterpreted by anything
downstream, so it survives exactly as written. See `rsx-transport` for why this
cannot be repaired client-side, and for what `extended_execute_luau_from_file`
now does about it.

The one-line console below is a trap for **reading** and the right answer for
**returning**. Both facts are true at once, and that inversion is worth stating
once because it is exactly what gets guessed wrong.

## Do not parse the console by line — match the pattern

`get_console_output`'s newline shape is **not stable, and both shapes have been
measured.** On some consumers the buffer arrives as one line with literal `\n`
escapes, so `splitlines()` finds one line where there are sixty. Through the
agent caller path it arrives with **real** newlines and `splitlines()` works
correctly (139-char sample, 2026-10-01).

An earlier version of this skill asserted the one-line shape universally. That
was wrong, and the code is what disproves it rather than a second measurement:
`extended_watch_output` **itself calls `splitlines()`** (`_ConsoleWatch.poll`),
so it could not return per-line output at all if the escaped shape were the only
one. The codebase assumes both, so a per-line parser here is wrong on one shape
or the other — and neither raises, which is why this has to be a rule rather
than something you discover.

**Match the pattern, do not parse lines.** This advice is unconditional, and it
is the part that matters:

- `re.findall(r"HIT i=(\d+)", console)` — correct on **both** shapes. Default to
  this.
- `splitlines()` plus a per-line scan — correct on one shape, silently wrong on
  the other. Never.
- Never a bare `re.findall(r"\d+", ...)` over the whole buffer either. Measured
  failure: a breakpoint test printing both `HIT i=1` and `TICK 5` came back as
  `15, 25, 35, ...` when the parser took every digit up to the next quote,
  swallowing interleaved output. It looked like a broken breakpoint; the
  breakpoint was fine and the parser was wrong.

**Need lines?** `extended_watch_output` splits them for you and returns
`{lines, returned, matched, total_seen, truncated}` — it does not return a bare
string. Use it instead of parsing the raw buffer, with one caveat worth knowing:
**its first poll returns only the last 20 lines**, and every poll is capped at
200. An agent that sets a breakpoint, polls once, and sees 20 lines with no hit
concludes there was no hit — which is the exact trap that made an earlier version
of this tool report `total_seen: 0` against a real Studio.

## Never print bulk data

A roughly 5 MB `print` permanently wedged both `get_console_output` and
`extended_watch_output` for one Studio instance. It did not recover, and the
only cure was restarting Studio.

A pixel dump, a base64 blob, a large JSON table, a long backtrace: none of it
belongs on the console. A console that has been wedged this way is unrecoverable
for the life of the process, so the cost is far higher than the bytes you saved.

## Report measurements through attributes instead

`inspect_instance` reads attributes on any instance, including studio-only ones.
`PluginGuiService` is the right parent: it exists only in Studio, never
replicates, and cannot reach the place file, a published place, or a team
create.

```lua
local PG = game:GetService("PluginGuiService")
local Players = game:GetService("Players")   -- bound, or this throws on paste
local holder = PG:FindFirstChild("RBXProbe") or Instance.new("Folder")
holder.Name = "RBXProbe"
holder.Parent = PG
holder:SetAttribute("players_after", tostring(#Players:GetPlayers()))
```

Then read it back with `inspect_instance` on
`game.PluginGuiService.RBXProbe`. Attributes cost nothing until read, survive
across tool calls, and leave the console clean for the lines a human will
actually look at.

Two rules that follow from this:

- prefer **string** attributes, since `tostring` output is directly comparable
  in a report.
- destroy the holder when you are done, so the next run starts clean and a
  stale attribute cannot be mistaken for a fresh reading.

## Use `extended_watch_output` in any loop

`get_console_output` re-sends the entire buffer every call. Polling it in a
loop is quadratic in the output size and will dominate your context.

`extended_watch_output` returns only lines since your last poll, tracked per
Studio instance. It is the right tool for following a run, and it is what
breakpoint hits ride on.

## Breakpoint hits and ordinary output share one stream

Because a logpoint hit is reported as an ordinary console line prefixed
`Breakpoint `, one polling tool covers logs, hits, and errors together. Filter
on the prefix rather than opening a second channel. See `rsx-breakpoints`.

## Recovering a wedged console

There is no recovery. `get_console_output` and `extended_watch_output` both stay
broken for that Studio process. Restart Studio. Prevention is the only fix, which
is why the rules above are absolute rather than advisory.
