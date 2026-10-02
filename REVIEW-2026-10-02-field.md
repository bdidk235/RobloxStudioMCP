# REVIEW-2026-10-02 — field review of RobloxStudioMCP after a day of real use

**Complements, does not duplicate, `REVIEW-2026-10-01.md`.** That pass was static:
dead code, docstrings that contradict their code, an orphaned sentence. It read the
tree. This one is the opposite — **I drove the tool for a full session and recorded
what actually went wrong.** Every finding below was reproduced at least twice unless
marked otherwise, and none of them are visible from reading the source.

**Test surface.** `stress-city` (a real 94-script Roblox game), one 19.6 MB `.rbxlx`,
three live Studios, `execute_luau` in all three datamodels, `extended_wait_for`,
`get_console_output`, `extended_manage_instance`, `extended_execute_luau_from_file`.

**Verdict: intuitive in its model, hostile in its diagnostics.** The verbs are clear
and the core primitive is genuinely good. The failure feedback points at the wrong
thing often enough that I spent three turns of a session blaming the tool for my own
bug. That inversion is the finding that matters; everything else is friction.

**Provenance labels.** `MEASURED` = reproduced and output quoted. `OBSERVED` = saw it
once, cause not isolated. `INFERRED` = reasoned, not run.

---

## 1. P0 — Errors point at the harness, not at the submitted script

`MEASURED`, three occurrences.

Every script failure returns as a stack rooted in Studio's own plugin:

```
sabuiltin_Assistant.rbxm.Assistant.Packages._Index.AssistantUI.AssistantUI.Tools.ExecuteLuauTool:66:
  sabuiltin_Assistant...AssistantUI.Util.CommandExecution:54:
  leaderstats is not a valid member of Player "Players.bdidk235"
```

The actionable part — the real message — is last, after two frames of studio-internal
locations that identify nothing I wrote. **The line number in my file is never
reported.** `CommandExecution:54` is a line in the plugin's module, identical for
every call, so it carries no information.

**What it cost.** A one-line bug (`player.leaderstats = folder` outside the `pcall` I
had just added around the *reads*) produced the same opaque string three times. I
concluded, in order: that `extended_execute_luau_from_file` was **caching** stale file
contents, then that `execute_luau` on the Client datamodel was **flaky**. Both were
wrong. The cache hypothesis is refuted in §3; the flakiness was me misreading my own
script. Only after rewriting the file from scratch did the bare write become visible.

**Fix.** Prefix script-level errors with the line offset in the submitted file.

```
repaired.rbxlx.lua:41: attempt to index nil (field 'ClientOnlyModules')
```

If the offset cannot be computed, say so explicitly rather than letting a plugin
location stand in for it. Also worth stripping the `sabuiltin_Assistant...` prefix
entirely — it is never actionable for a caller.

## 2. P1 — `get_console_output` is an unthrottled rolling buffer with no history

`MEASURED`, twice in one session.

Roblox's `TexturePack` upload spam (HTTP 429, rate-limited) filled the channel:

```
length: 20110   ->   bad lines: 68 (all "Failed to upload TexturePack ... 429")
length: 19917   ->   "ClientOnlyModules error present": false
```

Between those two calls the length changed and **the startup messages I needed were
gone** — `Server took 0.165s to load!`, `Created new data for bdidk235!`, and the
error I was actually hunting. Nothing indicated truncation had occurred; the failure
mode is silent loss at exactly the moment I look.

Asymmetry: reading is unbounded, but writing is fatal. A ~5 MB `print` permanently
wedges the channel for a Studio and only a restart clears it. So the tool punishes the
caller hard for one direction and silently loses data in the other.

**Fix.** Three cheap changes, in order of value:

1. **Ring buffer with an explicit cap** and a `{"dropped": N, "oldest": <ts>}` marker,
   so truncation is visible rather than inferred from a shrinking length.
2. **`grep` / `since` parameters.** Both errors I chased this session were one string
   ("is not a valid member", "must publish this place") in a 20 KB haystack. Returning
   matched lines with surrounding context would have made both a single call.
3. **A `warn`-level budget hint** when output exceeds N lines, aimed at the producer
   rather than the reader.

## 3. P2 — Two script-execution paths, undocumented relationship

`MEASURED`.

`execute_luau` (inline string) and `extended_execute_luau_from_file` (path) behave
identically on error, which is what made me hunt for a cache that does not exist.
I tested the cache question directly rather than assuming:

```
disk says MARKER-ONE  -> result "MARKER-ONE"
disk says MARKER-TWO  -> result "MARKER-TWO"
```

**No caching. It re-reads every call.** That is the right behaviour and it cost me two
turns to establish, because nothing in either tool's description says so and my
hypothesis was cheap to form.

**Fix.** One line in either description: *"reads the file fresh on every call; not
cached."* `extended_execute_luau_from_file` is the more useful of the two — it sidesteps
the 100,015-character return cap entirely, which is what makes long fixtures workable.

## 4. P3 — `execute_luau` fails intermittently with no parse detail

`OBSERVED`, Client datamodel only.

Three consecutive calls returned `Failed to parse command code` for a script that is
syntactically valid Luau, including a one-line `return "..."`. The same code then
succeeded unchanged, and identical code succeeded on `Server` throughout.

```
Client:  Failed to parse command code   x3   (same code)
Client:  identical code -> "server ok: true name=bdidk235"
Server:  identical code -> "server ok: true name=bdidk235"
```

If I had not retried, I would have recorded "Client datamodel is unusable" as a
finding. It is not — it is flaky, which is arguably worse, because a flaky tool gets
written off as broken or trusted until it isn't.

**Fix.** Include the parser's actual complaint and which span failed. If the plugin
cannot map it, distinguish "did not parse" from "plugin rejected" — those need
different responses from me.

## 5. P3 — `studio_id` is session-scoped and re-minted on every launch

`MEASURED`, three launches in one session: `5c6abef9…` → `37a2dd34…` → `39211df3…`.

The launch response says so, which is better than most tools manage:

> `studio_id is re-minted on every restart, so treat it as an address for this session only.`

But every subsequent call needs it, there is no alias, and it also returns
`"mesh_name": null` on a successful launch — the one field that could have served as a
stable label. I now keep three Studios' ids in my head across a session, and any
mismatch produces `NO_STUDIO`-class noise rather than a clear "that Studio closed."

**Fix.** Accept the place name where the id is currently required, or mint a caller-chosen
label at launch and resolve it. `extended_list_studios` already accepts
`resolve: { name }` — let the other tools do the same.

## 6. P4 — Nothing distinguishes a user's Studio from an agent-only one

`MEASURED` consequence, `INFERRED` cause.

The tool will happily Play a Studio a person is working in. I had to write the
distinction into my own agent instructions before touching Play:

> Never start Play without asking **on a Studio the user is working in.** A Studio you
> launched yourself, to test your own work, needs no question.

Nothing in the surface — not `list_roblox_studios`, not `manage_instance` — carries
ownership or provenance. `launch` knows it launched; it does not record that.

**Fix.** `launch` returns `"owned_by": "agent"` (or similar), and the listing carries
it. Small change, removes a whole class of guardrail prose.

## 7. P4 — Roblox errors pass through unannotated

`MEASURED`.

```
ClientOnlyModules is not a valid member of Folder "ReplicatedStorage.Modules"
```

This is Roblox's wording, not the MCP's, and it is a genuine and important find — it
is the whole reason a PlayerScript dies on every join. But nothing connects it to its
consequences: which script, which line, how often it runs, whether it is fatal. I had
to resolve it by walking the DataModel for the name myself.

**Fix.** Annotate where cheap: containing instance `GetFullName()`, script line if
available, and whether the error is repeating (a repeating error is almost always more
urgent than a one-off).

---

## 8. What is genuinely good — do not regress these

- **`extended_wait_for` is the best thing in the surface.** Being unable to sleep
  inside my own runtime is the single hardest constraint I work under, and this tool
  hands me a declarative escape from it: `os.clock() > 8`,
  `#Players:GetPlayers() >= 1`, `player:FindFirstChild("Data") ~= nil`. It returns
  structured diagnostics too (`satisfied`, `timed_out`, `polls`, `poll_errors`,
  `settled_repeats`), so a timeout tells me *why* rather than just failing. This is the
  primitive that made a stateful Studio session tractable at all.
- **Refusing to guess when ambiguous.** An omitted `studio_id` with several Studios
  attached errors out and *names the candidates*. The mirrored behaviour in
  `list_roblox_studios resolve` — ambiguous selectors return all candidates rather than
  picking one — is the right default and I rely on it.
- **`manage_instance action=stop` returns the pid and the way it resolved the target**
  ("matched the mesh name against each process's own log command line"). Rare, but
  exactly the provenance you want when you are about to kill a process.
- **Returning an arbitrary value from arbitrary Luau.** `return HttpService:JSONEncode(…)`
  with the caveat that raw tables lose array-ness is a strong design. It let me measure
  engine behaviour directly instead of inferring it: that is how I established
  `player.leaderstats` reads but rejects assignment, and that an untouched `Player` has
  no `leaderstats` child with `PlayerList` explicitly enabled — the control that stopped
  me reporting a phantom bug in the user's own code.
- **`skill` / `extended_skill` split.** Seven engine skills behind a live Studio id,
  transport skills behind none, with the ordering dependency stated. Correct, and the
  reason I stopped guessing at the wire format.

## 9. Guardrails I had to write because of the above

Each of these exists in my agent instructions *only* to work around this surface. They
are a symptom list:

| Guardrail | Works around |
|---|---|
| "A byte-identical error across code variants is in your code, not the tool" | §1 blame inversion |
| "Never print bulk data" — a ~5 MB print wedges the channel until restart | §2 unbounded write |
| "The console arrives as one line with literal `\n` escapes" | §2 formatting |
| "Returns truncate at exactly 100,015 characters, silently" | return cap, no marker |
| "Never cache an `studio_id` across sessions: it is re-minted on every restart" | §5 |
| "An omitted `studio_id` is refused when more than one Studio is connected" | §5 |

Six rules in one file, all downstream of this surface. Fixing §1 and §2 would let four
of them go.

## 10. If I were prioritising

1. **§1** — report the script line. Cheapest change, largest effect on my accuracy.
2. **§2** — `grep` + `since`, plus a visible `dropped` count. Makes both errors I chased
   this session one call instead of a forensic reconstruction.
3. **§5** — accept a stable label anywhere the id is taken.
4. **§6** — record launch provenance. Deletes a rule from every consumer's instructions.
5. **§3, §4** — two description and error-detail lines.
6. **§7** — annotate Roblox errors with instance path and repeat count.

None of these are architectural. They are the difference between a tool I trust and
one I second-guess — and right now, on a day where the tool was right and I was wrong
twice, that distinction was the whole story.