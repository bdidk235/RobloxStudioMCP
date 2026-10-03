# Type checking: pyright, and what it is actually worth

The reasoning behind `pyrightconfig.json` and `tests/test_typecheck.py`. Kept out
of the config because pyright treats an unrecognised key as a configuration
warning, and a config file full of prose is worse than a document next to it
anyway. Every claim here is measured; the raw output is reproducible.

## Why there is a gate at all

The worst bug found in this project was a wrong dictionary key.
`extended_watch_output` read `result["text"]` on a dict keyed `new_lines` /
`total_lines` / `last_line`. `.get("text", "")` supplied the default, the tool
returned `{"returned": 0}` on **every** call, and it reported `isError: false`.

It shipped because the only test on that tool asserted its *description*. The
surface was correct; nothing looked at the payload.

A previous single-language setup kept a compile-time checker as its build step,
and it caught four real errors in code written during a single session. Python
had **no type checker
at all** - 171 of 233 defs carried annotations that nothing validated. That
asymmetry was the thing worth closing.

## mypy or pyright

Both were installed and run against the whole package.

| | mypy 2.3.1 | pyright 1.1.414 |
|---|---|---|
| findings on `src/` | 9 - 8 real, 1 false positive | 7 - all real |
| elapsed | 6.1 s | 4.1-6.3 s |
| after fixing the 7 real defects | **1 error, and it is wrong** | **0 errors** |

They agreed on the seven real defects. mypy additionally reported
`instance.py:728`:

```python
started = {pid: when for pid, when in started.items() if when is not None}
identities = logid.live_identities([p["pid"] for p in all_processes], started)
# mypy: Argument 2 has incompatible type "dict[Any, float | None]"
```

That is a **false positive**. Line 727 has already filtered out `None`, so the
rebound dict genuinely is `dict[int, float]`. mypy narrows the loop variable in a
dict comprehension but not the resulting *container* type - a known limitation.
pyright got it right.

**pyright is the gate.** Same true-positive yield, better precision - and
precision is what decides whether a gate stays respected. A gate that cries wolf
gets turned off. mypy stays installed; it is still the better tool for
`reveal_type` when narrowing goes wrong.

## Which rules are errors, and why

Only rules that found something real on first run:

| rule | what it found |
|---|---|
| `reportOptionalMemberAccess` | 3 latent `None` dereferences |
| `reportArgumentType` | wrong types into `live_identities` and `str.join` |
| `reportOptionalIterable` | iterating a value that may be `None` |

`reportUnnecessaryIsInstance` and `reportRedeclaration` are **off** on purpose.
The codebase validates untrusted JSON at runtime, where a redundant `isinstance`
is defensive rather than redundant, and module-level re-exports legitimately
redeclare. Both are style opinions that would bury the three above.

## The honest limit

`typecheck_acid_test.py` tests the exact bug shape against both checkers, and the
result is the part worth remembering:

**Neither mypy nor pyright catches the bug as it was written.** With the return
type as bare `Dict[str, Any]` - and even with a correct `TypedDict` - a defaulted
`.get("text", "")` on a missing key is *legal in both*, because `dict.get` accepts
any key and any default by design.

Both checkers flagged the **subscript** form (`result["text"]`). Both flagged the
`None`-dereference class.

So the ordering is:

1. **The `TypedDict` is the fix.** `WatchResult` makes the three real keys
   visible in one place and catches the subscript form.
2. **The checker is the backstop** that keeps that annotation honest, and it does
   catch the `None`-dereference class, which no test in this suite would notice.
3. Neither alone would have caught the original line. Claiming otherwise would be
   selling the tooling.

`tests/test_typecheck.py` asserts all three: the TypedDict's keys, that
`watch_output` is annotated with it, and that it is **not** `Dict[str, Any]`.

## The gate has a negative control

`test_the_gate_would_actually_fail` feeds pyright the deliberately-broken acid
test and asserts a non-zero exit. A gate that cannot fail is decoration.

This earned its place immediately: the gate's own first version used to `skip`
when there were **no** diagnostics - which is the good case. It still blocked bad
code, but a green run reported "skipped" and looked like the check had not
happened. A gate you cannot distinguish from a gate that did not run is not a
gate.

## Running it

```bash
pip install pyright
python -m pytest tests/test_typecheck.py -q   # the gate
python -m pyright                              # direct
```

The test skips when pyright is absent, so the suite never blocks a contributor
who has not installed it.
