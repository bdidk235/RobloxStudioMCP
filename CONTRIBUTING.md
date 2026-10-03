# Contributing

Two clients for the same protocol — a Python original in `python/` and a
TypeScript port in `node/` — plus the evidence that justifies their design.
`README.md` covers what the project is and how to drive it. This file covers
working *on* it.

## The one-paragraph version

Every path goes through a platform layer. Every design decision is supposed to
be traceable to a measurement. The tool surface is identical on both sides and
enforced by a generated contract. Most of what looks like incidental complexity
here is load-bearing for one of those three facts, so read `TODO.md` before
"simplifying" anything.

## Setup

Two independent toolchains, each with its own working directory. Commands must
be run from the tree they belong to — they do not share a root.

```powershell
# Python (from python/)
pip install -e .[dev]

# Node (from node/)
pnpm install
```

Both are dependency-free at runtime: Python is stdlib-only on 3.9+, Node uses
built-ins only on 18+. The dev extras exist only for tests.

## Run the gates

A gate that has not been run is not a gate. Both suites run without Roblox
Studio — the tests use fakes throughout.

| Gate | Command | From |
| --- | --- | --- |
| Python behaviour | `python -m pytest tests -q` | `python/` |
| Python types | `pytest tests/test_typecheck.py` (runs pyright) | `python/` |
| Node types | `pnpm typecheck` | `node/` |
| Node behaviour | `npx vitest run` | `node/` |
| **Parity** | `pytest tests/test_parity.py` + `npx vitest run tests/parity.test.ts` | `python/`, `node/` |

### Two traps that will cost you a red build

**Never run bare `tsc --noEmit`.** The `typecheck` script is
`tsc --noEmit -p tsconfig.check.json` — a stricter project that *includes the
tests*. The bare invocation passes files the real gate rejects. Measured
2026-10-01: the difference shipped to `main` and turned CI red.

**`python -m unittest discover -s tests` is not the gate.** Several tests are
`pytest-asyncio`, and `unittest discover` does not drive them — it can report
green while skipping the async coverage. Use `pytest`.

### Parity is enforced, not aspirational

`parity/tools.json` is generated from the Python server by
`parity/build_contract.py` and asserted by **both** suites. A tool, a parameter
or a required argument cannot change on one side unnoticed. After a deliberate
surface change, regenerate it — the diff *is* the parity report:

```
python parity/build_contract.py
```

This also charges a **description budget**: a per-tool cap and a total cap, both
in the generated contract. The total list is paid on every call of every
session, so new surface is funded by trimming descriptions rather than raising
the cap. Read the live figures from `parity/tools.json` rather than from prose —
prose figures here have drifted before, and `test_parity.py` derives its own cap
from the contract so the gate cannot.

## Python and Node: same pass, no catch-up

Parity is closed at the **tool surface** on both sides. Behaviour underneath is
best-effort, and one difference is deliberate and documented in
`node/src/extended/IDENTITY.md`: `logid.ts` is ported and tested, but
`instance.ts` does not import it, so Node derives role and place from the
command line rather than the Studio's own log, and its `action=stop` **refuses**
rather than guessing across two Studios it cannot tell apart.

Two rules follow, and they are not the same rule:

- **Cheap, and the default:** a fix or feature lands on Python and Node in the
  same pass. This has cost almost nothing and it is how the parity work actually
  happened.
- **Expensive, and forbidden:** porting a Python-only module that Node lacks. The
  gap is a documented, frozen boundary, not debt to amortise.

## Evidence discipline

`TODO.md` is the project's institutional memory: what was measured, what is
inferred, what was retracted, what is open. Its central claim is that design
decisions are traceable to evidence rather than plausibility.

- **Tag provenance.** `MEASURED` / `DOCUMENTED` means it was executed or read
  here and the output is quoted. `INFERRED` is reasoning from a source that was
  not read. `UNVERIFIED` is a known unknown.
- **A grep that returns nothing is evidence about the grep, not the system.**
  This has bitten this file repeatedly, and twice produced a confident mechanism
  asserted from a nearby observation.
- **Record retractions rather than deleting them.** The file's own warning is
  that several plausible-sounding entries are already retracted. One such note
  (`TODO.md`, wrong-Studio capture) sat *beside* its own correction in a single
  commit and was left telling a contributor to revert a correct fix.
- **Do not let a rule live in two files without a check.** `AGENTS.md` and
  `TODO.md` both carry the Studio-instance constraint; a rule corrected in one
  and not the other is a rule that lies in the other.

## Tests that need judgement, not just coverage

Several gates exist specifically to catch **silent** wrong answers, which is the
failure class this project keeps paying for:

- **Unknown parameters are refused before dispatch**, on both sides. A silently
  ignored argument produces a plausible wrong answer *and reports success* —
  three separate incidents here did exactly that. Relayed Studio tools are
  deliberately exempt, because this project cannot add parameters to them.
- **`test_closed_sets.py`** checks exhaustiveness over the closed sets: every
  error code is producible, every advertised `action` is handled rather than
  merely accepted. This is what found an error code that was unreachable, which
  no type checker on either side would have caught.
- **Build freshness is a test**, not a habit. `dist/` older than `src/` runs the
  old code with no error anywhere, so it has a negative control proving the check
  can fail.

## Platform support

**Windows is the tested platform. macOS is supported but unproven. Linux is not
supported** — there is no POSIX branch, and that is a missing layer rather than
a bug. All platform differences are confined to
`python/src/roblox_studio_mcp/extended/platform.py` and
`node/src/extended/platform.ts`, so a port has a known shape and a known size.

The macOS branch has never been run against a real macOS Studio. Treat a macOS
bug report as a genuine unknown, and check `TODO.md` before concluding anything
about macOS is broken.

## Commit signing

Commits are SSH-signed and `commit.gpgsign` is on, so a plain `git commit` is
already signed. **Do not pass `--no-gpg-sign`** — that was a workaround for a
key that did not exist, and the workaround outlived its cause.

If a commit shows as unverified on GitHub, the public key is not registered
under *Signing keys* (a different list from *Authentication keys*). Note that
`git verify-commit` **cannot** detect that: it proves the signature is sound,
not that GitHub recognises it. Only a pushed commit answers that.

## Working with a live Studio

Any Studio instance you did not launch yourself is off limits unless a user puts
it in scope explicitly. This is a standing constraint, not a style preference —
the full rule and its reasoning are at the top of `TODO.md` and in `AGENTS.md`
rule 1. It governs reads as well as writes.