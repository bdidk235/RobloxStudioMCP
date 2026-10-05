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
Same true-positive yield, better precision, so pyright is the gate - and
precision is what decides whether a gate stays respected.

**The honest limit.** Neither checker would have caught the `watch_output` bug as
written, because a defaulted ``.get()`` on a missing key is legal in both - see
`typecheck_acid_test.py`, which measures exactly that. What catches that class is
the ``TypedDict`` on ``WatchResult``, and this gate is the backstop that keeps the
annotation honest once it exists. Both are needed; neither is sufficient alone.
"""

import json
import os
import subprocess
import sys
import unittest

# Three dirnames: this file is python/tests/test_typecheck.py, so two would stop
# at `python/`. Same trap as in test_contract.py, and it failed the same way.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PACKAGE = os.path.join(_ROOT, "python", "src", "roblox_studio_mcp")
_CONFIG = os.path.join(_ROOT, "python", "pyrightconfig.json")


def _has_pyright() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import pyright"],
        capture_output=True, timeout=120,
    )
    return probe.returncode == 0


@unittest.skipUnless(_has_pyright(), "pyright is not installed; run: pip install pyright")
class TypeCheckGate(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
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

    def _payload(self) -> dict:
        assert self.payload is not None, "pyright produced no JSON"
        return self.payload

    def test_the_config_exists_and_is_valid_json(self):
        self.assertTrue(os.path.isfile(_CONFIG), "pyrightconfig.json is missing")
        with open(_CONFIG, encoding="utf-8") as handle:
            json.load(handle)

    def test_the_checker_actually_ran(self):
        """A gate that silently checked nothing is worse than no gate."""
        self.assertIsNotNone(
            self.payload,
            "pyright produced no JSON; the gate would pass without checking",
        )
        self.assertGreater(self._payload()["summary"]["filesAnalyzed"], 10)

    def test_no_type_errors(self):
        """A clean run must PASS, not skip.

        The first version of this test skipped when there were no diagnostics -
        which is the *good* case. The gate still blocked bad code, but a green
        run reported "skipped" and looked like the check had not happened. A gate
        that cannot be distinguished from a gate that did not run is not a gate.
        """
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

    def test_the_gate_would_actually_fail(self):
        """Negative control. A gate that cannot fail is decoration, so this
        proves the checker reports errors at all by feeding it a file that has
        one. Uses the acid-test file, which contains deliberate errors."""
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
        import inspect

        from roblox_studio_mcp.extended import extensions

        # `from __future__ import annotations` is in effect, so the raw
        # annotation is the *string* "WatchResult", not the class. Comparing
        # against the class is a test that cannot pass - which is how it failed
        # the first time.
        raw = inspect.get_annotations(extensions.watch_output, eval_str=False)
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
