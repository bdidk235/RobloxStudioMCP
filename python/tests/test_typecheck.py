"""The type-checking gate.

**Why this exists.** The worst bug found in this project was a wrong dictionary
key: `extended_watch_output` read `result["text"]` on a dict keyed `new_lines`,
returned `{"returned": 0}` on every call, and reported `isError: false`. It
shipped because the only test on that tool asserted its *description*.

A prior single-language history note: a compile-time check once caught
four real errors in code written during one session. Python had **no type
checker at all** - 171 of 233 defs carried annotations that nothing validated.
That asymmetry is the thing this file closes.

**mypy or pyright?** Both were installed and run against the whole package, and
the results are recorded in `TODO.md`. Both found the same seven real defects.
mypy additionally reported `instance.py:728`, which is a **false positive**: it
narrows the loop variable in a dict comprehension but not the resulting container
type, and line 727 has already filtered `when is not None`. pyright was right.
Same true-positive yield, better precision, so pyright held the gate - and
precision is what decides whether a gate stays respected.

**ty or pyright?** ty is the default backend (`ROBLOX_TYPECHECKER` unset):
**0.40 s against pyright's 12.96 s** on this package, measured 2026-10-10, and
it caught the same live defect pyright caught (`Optional[str]` passed where
`str` was required, at the exact line) plus the acid-test errors. CI pins
`ROBLOX_TYPECHECKER=pyright` for the full suite and additionally runs this
gate under a pinned ty (`ty==0.0.85` in `[dev]`), so both backends are proven
on every push - the pin insulates CI from 0.0.x diagnostic drift. Both backends must report zero on a clean tree;
the four ty diagnostics on first contact were resolved with no rule suppressed:
one dead file deleted (a stray module shadowed by the package directory of the
same name and imported by nothing), one `Dict[str, Any]` annotation, and one
platform ignore comment next to pyright's own on the same line.

**The honest limit.** Neither checker would have caught the `watch_output` bug as
written, because a defaulted ``.get()`` on a missing key is legal in both - see
`typecheck_acid_test.py`, which measures exactly that. What catches that class is
the ``TypedDict`` on ``WatchResult``, and this gate is the backstop that keeps the
annotation honest once it exists. Both are needed; neither is sufficient alone.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Optional

# Three dirnames: this file is python/tests/test_typecheck.py, so two would stop
# at `python/`. Same trap as in test_contract.py, and it failed the same way.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PACKAGE = os.path.join(_ROOT, "python", "src", "roblox_studio_mcp")
_CONFIG = os.path.join(_ROOT, "python", "pyrightconfig.json")

#: Which checker this gate runs. ty is the default because it is the fast one;
#: CI pins `pyright` because ty is 0.0.x and its diagnostics may move between
#: releases. Anything else is a misspelling, and a misspelled backend selecting
#: the other checker silently would be a gate that did not run - so setUpClass
#: fails loudly on it instead of defaulting.
def _checker() -> str:
    choice = os.environ.get("ROBLOX_TYPECHECKER", "ty").strip().lower()
    assert choice in ("ty", "pyright"), (
        "ROBLOX_TYPECHECKER=%r is not a checker this gate knows; "
        "use 'ty' or 'pyright'" % os.environ.get("ROBLOX_TYPECHECKER")
    )
    return choice


def _has_pyright() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import pyright"],
        capture_output=True, timeout=120,
    )
    return probe.returncode == 0


def _ty_bin() -> str:
    """The `ty` executable, resolved the same way `_has_ty` probes.

    `subprocess` inherits `PATH`, not the interpreter's directory, so passing
    the bare name reintroduces the miss the probe just fixed: found, then
    `FileNotFoundError` at exec. Resolve once, use everywhere.
    """
    here = os.path.join(os.path.dirname(sys.executable), "ty")
    if os.path.isfile(here):
        return here
    found = shutil.which("ty")
    assert found is not None, "ty passed the probe but is not on PATH"
    return found


def _has_ty() -> bool:
    # ty ships as a binary console script, not an importable module, so there
    # is no `-m` spelling. `shutil.which` alone is not enough: the test runs
    # is no `-m` spelling. `shutil.which` alone is not enough: the test runs
    # under a venv interpreter whose `bin/` may not be on PATH, so look next
    # to the running interpreter first. That miss cost a full silent skip -
    # every gate test skipped, suite still green - which is exactly the failure
    # this gate exists to prevent.
    here = os.path.join(os.path.dirname(sys.executable), "ty")
    if os.path.isfile(here):
        return True
    return shutil.which("ty") is not None


def _require_checker() -> str:
    """The selected backend, or SkipTest when it is not installed.

    Raising (rather than returning a skip) keeps the "not installed" case a
    skip and the misspelled-backend case a loud failure: `_checker` asserts
    membership, so an unknown `ROBLOX_TYPECHECKER` fails here instead of
    silently running the other checker.
    """
    checker = _checker()
    if checker == "ty" and not _has_ty():
        raise unittest.SkipTest("ty is not installed; run: pip install ty")
    if checker == "pyright" and not _has_pyright():
        raise unittest.SkipTest(
            "pyright is not installed; run: pip install pyright"
        )
    return checker


class TypeCheckGate(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.checker = _require_checker()
        if cls.checker == "pyright":
            cls._run_pyright()
        else:
            cls._run_ty()

    @classmethod
    def _run_pyright(cls):
        # No path argument: pyright then uses `include` from pyrightconfig.json.
        # Passing a directory overrides that and sweeps in tests/ and the acid
        # test, which legitimately contain deliberate errors and fake clients.
        proc = subprocess.run(
            [sys.executable, "-m", "pyright", "--outputjson"],
            cwd=os.path.join(_ROOT, "python"),
            capture_output=True,
            text=True,
            timeout=900,
        )
        cls.returncode = proc.returncode
        try:
            cls.payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            cls.payload = None
        cls.ty_output = None

    @classmethod
    def _run_ty(cls):
        # Concise is one diagnostic per line, so the gate counts `error[` lines
        # rather than parsing a schema. The scope is the package, not the
        # project root: pyright's `include` covers `src/roblox_studio_mcp` plus
        # `../contract`, and contract JSON has no types to check, so the
        # package alone is the shared scope.
        proc = subprocess.run(
            [_ty_bin(), "check", "--output-format", "concise",
             "src/roblox_studio_mcp"],
            cwd=os.path.join(_ROOT, "python"),
            capture_output=True,
            text=True,
            timeout=900,
        )
        cls.returncode = proc.returncode
        cls.ty_output = proc.stdout or ""
        cls.payload = None

    def _payload(self) -> dict:
        assert self.payload is not None, "pyright produced no JSON"
        return self.payload

    def test_the_config_exists_and_is_valid_json(self):
        self.assertTrue(os.path.isfile(_CONFIG), "pyrightconfig.json is missing")
        with open(_CONFIG, encoding="utf-8") as handle:
            json.load(handle)

    def test_the_checker_actually_ran(self):
        """A gate that silently checked nothing is worse than no gate."""
        if self.checker == "pyright":
            self.assertIsNotNone(
                self.payload,
                "pyright produced no JSON; the gate would pass without checking",
            )
            self.assertGreater(self._payload()["summary"]["filesAnalyzed"], 10)
        else:
            # ty exits 0 clean and 1 with diagnostics; anything else is a usage
            # or crash failure, and empty output is a run that said nothing.
            self.assertIn(
                self.returncode, (0, 1),
                "ty exited %s, which is neither clean nor diagnostics" % self.returncode,
            )
            self.assertTrue(
                (self.ty_output or "").strip(),
                "ty produced no output; the gate would pass without checking",
            )

    def test_no_type_errors(self):
        """A clean run must PASS, not skip.

        The first version of this test skipped when there were no diagnostics -
        which is the *good* case. The gate still blocked bad code, but a green
        run reported "skipped" and looked like the check had not happened. A gate
        that cannot be distinguished from a gate that did not run is not a gate.
        """
        if self.checker == "pyright":
            diagnostics = [
                d for d in self._payload().get("generalDiagnostics", [])
                if d.get("severity") == "error"
            ]
            rendered = [
                "%s:%s  %s  [%s]" % (
                    d["file"].replace(_ROOT + os.sep, ""),
                    d["range"]["start"]["line"] + 1,
                    d["message"].splitlines()[0],
                    d.get("rule", "?"),
                )
                for d in diagnostics
            ]
            self.assertEqual(
                len(diagnostics), 0,
                "type errors:\n  " + "\n  ".join(rendered),
            )
        else:
            errors = [
                line for line in (self.ty_output or "").splitlines()
                if "error[" in line
            ]
            self.assertEqual(
                self.returncode, 0,
                "ty exited %s:\n  %s" % (self.returncode, self.ty_output),
            )
            self.assertEqual(errors, [], "type errors:\n  " + "\n  ".join(errors))

    def test_the_gate_would_actually_fail(self):
        """Negative control. A gate that cannot fail is decoration, so this
        proves the checker reports errors at all by feeding it a file that has
        one. Uses the acid-test file, which contains deliberate errors."""
        if self.checker == "pyright":
            proc = subprocess.run(
                [sys.executable, "-m", "pyright", "--outputjson",
                 os.path.join("scripts", "typecheck_acid_test.py")],
                cwd=os.path.join(_ROOT, "python"),
                capture_output=True,
                text=True,
                timeout=900,
            )
            self.assertNotEqual(
                proc.returncode, 0,
                "pyright exited 0 on a file with known type errors, so the gate is not "
                "capable of failing",
            )
            payload = json.loads(proc.stdout)
            errors = [
                d for d in payload.get("generalDiagnostics", [])
                if d.get("severity") == "error"
            ]
            self.assertGreaterEqual(
                len(errors), 2,
                "expected the deliberate errors in typecheck_acid_test.py to be reported",
            )
        else:
            # ty gets a file of its own rather than the acid test: the acid
            # test is written against pyright's rules, and a negative control
            # must fail for a reason the backend under test understands.
            with tempfile.TemporaryDirectory() as tmp:
                victim = os.path.join(tmp, "deliberate_errors.py")
                with open(victim, "w", encoding="utf-8") as handle:
                    handle.write(
                        'answer: int = "not an int"\n'
                        "print(undefined_name_for_the_gate)\n"
                    )
                proc = subprocess.run(
                    [_ty_bin(), "check", "--output-format", "concise", victim],
                    cwd=os.path.join(_ROOT, "python"),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            self.assertNotEqual(
                proc.returncode, 0,
                "ty exited 0 on a file with known type errors, so the gate is not "
                "capable of failing",
            )
            errors = [
                line for line in (proc.stdout or "").splitlines()
                if "error[" in line
            ]
            self.assertGreaterEqual(
                len(errors), 2,
                "expected both deliberate errors to be reported, got:\n  "
                + (proc.stdout or "").strip(),
            )

    def test_the_optional_deref_class_stays_enabled(self):
        """The rule that found three latent None dereferences. If it is ever
        turned off, this fails - because the class of bug it catches is invisible
        to every test in this suite."""
        with open(_CONFIG, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(config.get("reportOptionalMemberAccess"), "error")


class TypedDictIsTheRealFix(unittest.TestCase):
    """The annotation, not the checker, is what prevents the shipped bug.

    This is a documentation test: it asserts the return type is specific enough
    that a *subscript* on a wrong key is a compile error, which is the form the
    checker can see.
    """

    def test_watch_result_declares_its_three_keys(self):
        from roblox_studio_mcp.extended.extensions import WatchResult

        self.assertEqual(
            set(WatchResult.__annotations__),
            {"new_lines", "total_lines", "last_line"},
        )

    def test_watch_output_is_annotated_with_it(self):
        from roblox_studio_mcp.extended import extensions

        # `from __future__ import annotations` is in effect, so the raw
        # annotation is the *string* "WatchResult", not the class. Comparing
        # against the class is a test that cannot pass - which is how it failed
        # the first time. `inspect.get_annotations(..., eval_str=False)` is the
        # 3.10+ spelling of this; `__annotations__` is the same dict on 3.9.
        raw = extensions.watch_output.__annotations__
        self.assertEqual(raw.get("return"), "WatchResult")

    def test_it_resolves_to_the_typed_dict(self):
        """The stronger form: resolve the string and check the actual class, so
        renaming `WatchResult` without updating the annotation fails here."""
        import typing

        from roblox_studio_mcp.extended import extensions

        resolved = typing.get_type_hints(extensions.watch_output)
        self.assertIs(resolved["return"], extensions.WatchResult)

    def test_it_is_not_a_bare_dict_any(self):
        """`Dict[str, Any]` is what let the wrong key through unnoticed: every
        key access is legal against it."""
        import typing

        from roblox_studio_mcp.extended import extensions

        resolved = typing.get_type_hints(extensions.watch_output)
        self.assertIsNot(resolved["return"], typing.Dict[str, typing.Any])


if __name__ == "__main__":
    unittest.main()
