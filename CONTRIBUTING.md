# Contributing

A Python client for the Studio MCP protocol in `python/`, plus the evidence
that justifies its design.
`README.md` covers what the project is and how to drive it. This file covers
working *on* it.

## The one-paragraph version

Every path goes through a platform layer. Every design decision is supposed to
be traceable to a measurement. The tool surface is pinned by a generated
contract. Most of what looks like incidental complexity
here is load-bearing for one of those three facts, so read `TODO.md` before
"simplifying" anything.

## Setup

Commands must be run from `python/` — there is a single toolchain with a
single working directory.

```powershell
# Python (from python/)
pip install -e .[dev]
```

It is dependency-free at runtime: stdlib-only on 3.9+. The dev extras exist
only for tests.

## Run the gates

A gate that has not been run is not a gate. The suite runs without Roblox
Studio — the tests use fakes throughout.

| Gate | Command | From |
| --- | --- | --- |
| Python behaviour | `python -m pytest tests -q` | `python/` |
| Python types | `pytest tests/test_typecheck.py` (runs pyright) | `python/` |
| Contract | `pytest tests/test_contract.py` | `python/` |

**`python -m unittest discover -s tests` is not the gate.** Several tests are
`pytest-asyncio`, and `unittest discover` does not drive them — it can report
green while skipping the async coverage. Use `pytest`.

### The contract is enforced, not aspirational

`contract/tools.json` is generated from the Python server by
`contract/build_contract.py` and asserted by the suite. A tool, a parameter
or a required argument cannot change unnoticed. After a deliberate
surface change, regenerate it - the diff *is* the contract report:

```
python contract/build_contract.py
```

This also charges a **description budget**: a per-tool cap and a total cap, both
in the generated contract. The total list is paid on every call of every
session, so new surface is funded by trimming descriptions rather than raising
the cap. Read the live figures from `contract/tools.json` rather than from prose —
prose figures here have drifted before, and `test_contract.py` derives its own cap
from the contract so the gate cannot.

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

- **Unknown parameters are refused before dispatch**. A silently
  ignored argument produces a plausible wrong answer *and reports success* —
  three separate incidents here did exactly that. Relayed Studio tools are
  deliberately exempt, because this project cannot add parameters to them.
- **`test_closed_sets.py`** checks exhaustiveness over the closed sets: every
  error code is producible, every advertised `action` is handled rather than
  merely accepted. This is what found an error code that was unreachable, which
  no type checker would have caught.

## Platform support

**Windows is the tested platform. macOS is supported but unproven. Linux is not
supported** — there is no POSIX branch, and that is a missing layer rather than
a bug. All platform differences are confined to
`python/src/roblox_studio_mcp/extended/platform.py`.

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