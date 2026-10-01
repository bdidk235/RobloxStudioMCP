# Identity: where Node and Python still differ

**The tool surface is at parity. This file is about behaviour underneath it.**

`parity/tools.json` is asserted by both test suites
(`python/tests/test_parity.py`, `node/tests/parity.test.ts`), so both servers
expose the same 16 tools with the same schemas. That is enforced, not hoped for.

What parity does **not** cover is the one place the two servers genuinely differ,
and it is worth being precise about rather than letting "parity" imply more than
it means.

## The gap: how a `studio_id` becomes a PID

| | Python | Node |
|---|---|---|
| method | reads the PID out of that Studio's own log file | process enumeration + a time window |
| module | `logid.py` (33 KB) | not ported |
| `action=list` | resolves every process to a place | resolves role and place *from the command line* |
| two Studios, one place | resolves both, correctly | reports them, but **cannot say which is which** |
| `action=stop` | resolves `studio_id` -> PID and stops it | **refuses** when more than one Studio is attached |

### Why Python needs the log, and why that matters

The mesh row carries **only `id` and `name`** - checked directly, not inferred.
So resolving `studio_id` to a process needs a second, independent record. Python's
chain is:

```
studio_id -> mesh name (basename) -> that process's log command line
          -> that log's PID line -> PID
```

The load-bearing step is the last one: a Studio log states its own PID in
`[FLog::UIThreadNotifier] Constructing UIThreadNotifier for process 'N'`. Found in
**64 of 66** logs. No console write is needed, which is why the full
launch -> identify -> resolve -> stop cycle leaves the console byte-identical
(73 chars, 0 growth).

### What is already on the Node side

`node/src/extended/platform.ts` carries process enumeration, mesh attachment via
`Get-NetTCPConnection` / `lsof`, termination, and the path helpers. The launch
URI is ported and tested, including the measured constraints: exactly four keys,
both ids required, `universeId:0` as the value that fetches, and
`platform`-relative `isWindows()` branching.

### What is not

`logid.py`'s log parsing: the 256 KB prefix read, the filename-stamp ordering, the
three place-argument spellings, the `PlaceSessionId` GUID fallback, and the
command-line parser. Porting it is mechanical but it is 33 KB of the most
subtle code in the project, and a *partial* port would be worse than none - a log
with no PID would report "no identity" with no error, which is the exact failure
shape this project keeps paying for.

### The current behaviour, deliberately

Node's `action=stop` with more than one attached Studio raises rather than
guessing:

> ambiguous: N attached Studio processes are open, and the log-based identity
> chain that would tell them apart is not ported to Node yet. Use the Python
> server for action=stop on a multi-Studio machine.

Two Studios open on one place is not an exotic case on this machine - it is the
current state, both reporting mesh name `Place1`. A wrong answer here terminates
the wrong process, and `action=stop` is irreversible.

### Closing it

Port `logid.py` and `locks.py`, then delete this file. The contract test already
holds the surface still; nothing else needs to change.
