# Identity: where Node and Python still differ

**The tool surface is at parity. This file is about behaviour underneath it.**

`parity/tools.json` is asserted by both test suites
(`python/tests/test_parity.py`, `node/tests/parity.test.ts`), so both servers
expose the same 16 tools with the same schemas. That is enforced, not hoped for.

Parity does **not** extend to how a `studio_id` becomes a PID. That is the one
real behavioural difference left, and it is worth stating precisely rather than
letting "parity" imply more than it means.

## The gap is the wiring, not the code

An earlier version of this file said `logid.py` was "not ported" and told the
reader to port it. **That was wrong.** `logid.ts` (86 KB, against Python's
69 KB) exists and `node/tests/logid.test.ts` exercises it in 60-odd places.
`locks.ts` is ported too.

What is missing is narrower: **`instance.ts` never imports `logid.ts`.**

| | Python | Node |
|---|---|---|
| `logid.py` / `locks.py` | original | **ported** |
| used by `instance.ts` | yes | **no** |
| `action=list` resolves a place | from the Studio's own log | from the **command line** (`processRows`, `roleFromCommandLine`, `placeFromCommandLine`) |
| two Studios, one place | tells them apart | reports both, cannot say which is which |
| `action=stop` | `studio_id` -> PID, then stops it | needs a **PID**; it does not resolve one |

So `stopProcess(pid)` works on Node — it just has nothing to hand it. The
identity chain Python uses is:

```
studio_id -> mesh name (basename) -> that process's log command line
          -> that log's PID line -> PID
```

The load-bearing step is the last: a Studio log states its own PID in
`[FLog::UIThreadNotifier] Constructing UIThreadNotifier for process 'N'`. Found in
**64 of 66** logs. No console write is needed, which is why the full
launch -> identify -> resolve -> stop cycle leaves the console byte-identical.

The mesh row carries **only `id` and `name`** — checked directly, not inferred —
which is why a second, independent record is needed at all.

## Where Node does refuse, and where

The refusal is real but it is **not** in `instance.ts`. It is in `roblox.ts`:
an unpinned `studio_id` is resolved from `list_roblox_studios` on every call and
accepted only when exactly one Studio is connected. Zero throws; more than one
throws, because list order is the proxy mesh's and carries no intent.

Resolving on every call is deliberate — a cached id would keep being used after
a second Studio opened, which is the ambiguity being refused.

## What is already on the Node side

`platform.ts` carries process enumeration, mesh attachment via
`Get-NetTCPConnection` / `lsof`, termination, and the path helpers. The launch
URI is ported and tested, including its measured constraints: exactly four keys,
**both** ids required, and dispatch through the registered protocol handler.

The universe id is derived, not assumed — `resolveUniverseId()` fetches it from
the place id with retries. An earlier claim that `universeId: 0` "is the value
that fetches" was **withdrawn**: 8 of 8 launches on the real id succeeded, and 6
of 6 on 0, with no errors either way. `URI_UNIVERSE_ID = 0` remains as an
accepted explicit value, not as a rule. See `docs/EVIDENCE.md`.

## Closing it

Import `logid.ts` in `instance.ts` and use it where `processRows()` currently
supplies role and place. The pieces are written and tested; what is missing is
the call site, plus a decision about what to do when the log yields no PID —
which must be an error, never a silent "no identity", since that failure shape
is the one this project keeps paying for.

The contract test already holds the surface still; nothing else needs to change.