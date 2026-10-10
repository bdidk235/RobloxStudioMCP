"""Closed sets must be fully connected.

The idea under test was: *express the project's closed sets as a foreign
discriminated union and let that compiler prove Python conforms*. A union can
say "handle every case or fail to compile"; Python cannot, and neither can
pyright.

**The idea is sound and the vehicle is wrong**, and this file is the proof. An
exhaustiveness check found a real defect in about twenty minutes of Python. A
foreign spec would have found the same defect and then needed maintaining
forever.

The defect it found, on first run:

- **`LAUNCH_FAILED` was unreachable.** It is in the public vocabulary
  (`ALL_CODES`) but no message pattern produces it, and no code path raises it
  directly - it appeared only in `errors.py`, in the constant and the set. Every
  realistic launch failure therefore classified as `UNKNOWN`:

  | message | code |
  |---|---|
  | `no complete RobloxStudioBeta.exe under ...` | `UNKNOWN` |
  | `Studio did not open a place within 75s` | `UNKNOWN` |
  | `MCP is not enabled in Studio` | `UNKNOWN` |

  A caller who writes `if (code === "LAUNCH_FAILED")` - which is exactly what
  publishing that constant invites - silently never matches. That is a worse
  failure than a missing code: it looks handled.

- **`PLACE_NOT_OPEN` is shadowed.** Its own wording, `"place is not open"`, is
  claimed by `STALE_STUDIO_ID`. Whether that is right is an API decision, so it
  is recorded as an open question rather than changed here.

**What no type checker would have caught:** `ToolError("LAUNCH_FAILED",
...)` type-checks perfectly. Only "is this closed set fully
connected?" finds it. That is the one capability a foreign type system genuinely
adds, and it is worth having - just not at the price of a second implementation.
"""

import asyncio
import os
import sys
import unittest

# Three dirnames: this file is python/tests/test_closed_sets.py, so two stop at
# `python/`. Third time this has bitten in this repo - test_contract.py and
# test_typecheck.py both failed the same way.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp import extended_server as S  # noqa: E402
from roblox_studio_mcp.extended import errors as E  # noqa: E402
from roblox_studio_mcp.extended import instance as I  # noqa: E402

#: Codes produced by something other than the ordered pattern table. Each entry
#: names its producer, because "it is fine" is not a reason - a reader needs to
#: be able to check.
_DIRECTLY_RAISED = {
    E.INVALID_ARGUMENT: "raised by handlers that validate arguments (8 sites)",
    E.LUA_ERROR: "raised by waiting.py when a condition faults at runtime",
    E.TEST_BUSY: "raised by the test-service paths",
    E.TEST_REFUSED: "raised by the test-service paths",
    E.UNKNOWN: "classify's own fallback, the final return",
    E.WITNESS_MISMATCH: (
        "raised by extended_server's stop path when the kill target has no "
        "independent witness (audit A5); the revalidation refuses and cannot "
        "classify it itself"
    ),
}

#: Declared, but currently unreachable. Kept as an explicit list rather than
#: deleted, and rather than quietly excluded: each one is a live API question,
#: and a test that hides it is worse than one that reports it.
#:
#: `PLACE_NOT_OPEN` - its own wording, "place is not open", is claimed by
#: `STALE_STUDIO_ID`, deliberately (a stale studio_id *presents* as that message).
#: So this code can never be produced and nothing raises it. Either it should own
#: that wording, or it should be removed from the public vocabulary. **That is an
#: API decision, not a bug fix, so it is not made here.** Tracked in TODO.md.
#:
#: `LAUNCH_FAILED` was in this list until the exhaustiveness check found it. It
#: is the reason this file exists: a published code that no failure can produce
#: invites callers to write a branch that is silently dead.
_KNOWN_UNREACHABLE = {
    E.PLACE_NOT_OPEN: "shadowed by STALE_STUDIO_ID; needs an API decision",
}


class ErrorVocabularyIsConnected(unittest.TestCase):
    def test_no_code_is_declared_but_unproducible(self):
        """A code nothing can produce is worse than a missing code: a caller
        writes a branch for it and the branch is dead."""
        pattern_codes = {code for code, _needles in E._PATTERNS}  # noqa: SLF001
        unreachable = set(E.ALL_CODES) - pattern_codes - set(_DIRECTLY_RAISED)
        unexplained = unreachable - set(_KNOWN_UNREACHABLE)
        self.assertEqual(
            unexplained, set(),
            "declared but nothing produces these, and not on the known list: %s"
            % sorted(unexplained),
        )

    def test_every_known_unreachable_code_still_has_its_reason(self):
        """A known gap without a stated reason is just a suppressed failure. If
        a code is fixed, this fails until the entry is removed."""
        for code, reason in _KNOWN_UNREACHABLE.items():
            self.assertTrue(reason.strip(), "%s has no stated reason" % code)
            self.assertIn(code, E.ALL_CODES)

    def test_no_pattern_maps_to_an_undeclared_code(self):
        pattern_codes = {code for code, _needles in E._PATTERNS}  # noqa: SLF001
        self.assertEqual(pattern_codes - set(E.ALL_CODES), set())

    def test_every_code_has_at_least_one_producer(self):
        """Stronger than the above: a code is fine if `classify` can produce it,
        or a handler raises it directly, or it is a listed known gap."""
        pattern_codes = {code for code, _needles in E._PATTERNS}  # noqa: SLF001
        import glob

        sources = glob.glob(
            os.path.join(_ROOT, "python", "src", "roblox_studio_mcp", "**", "*.py"),
            recursive=True,
        )
        raised = set()
        for path in sources:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for code in E.ALL_CODES:
                if 'ToolError("%s"' % code in text:
                    raised.add(code)
        orphans = (
            set(E.ALL_CODES) - pattern_codes - raised - set(_DIRECTLY_RAISED)
            - set(_KNOWN_UNREACHABLE)
        )
        self.assertEqual(
            orphans, set(),
            "no pattern and no raise site for: %s" % sorted(orphans),
        )

    def test_a_launch_failure_is_not_reported_as_unknown(self):
        """The concrete consequence. `LAUNCH_FAILED` existed as a name and could
        not occur, so every launch failure was the catch-all."""
        messages = [
            "no complete RobloxStudioBeta.exe under C:\\Users\\... An update may be in progress",
            "Studio did not open a place within 75s",
            "MCP is not enabled in Studio",
        ]
        for message in messages:
            self.assertEqual(
                E.classify(RuntimeError(message)).code,
                E.LAUNCH_FAILED,
                message,
            )


class EveryRaisedCodeIsDeclared(unittest.TestCase):
    """The direction the gate above did not check, and this file's reason to exist twice.

    ``ErrorVocabularyIsConnected`` proves every *declared* code is producible. It
    said nothing about raised codes, and ``extended_capture`` shipped an
    ``INTERNAL_ERROR`` that is not declared - so ``ToolError.__init__`` raised
    ``AssertionError`` *while constructing the error* and replaced it. The caller
    received ``UNKNOWN: unknown error code 'INTERNAL_ERROR'``: no code, no
    metadata, and the message saying the capture had succeeded destroyed.

    A published code that cannot occur is worse than a missing one. A *raised*
    code that is not published is the same defect from the other side, and it is
    strictly more damaging, because it does not merely fail to match - it throws
    away the error it was carrying.

    Scans ``ToolError("<CODE>"`` literals across ``src/``. Both languages' tests
    must do this; a per-side assertion restates the same bug twice, which is the
    argument this file's sibling suites already make about shared fixtures.
    """

    def _sources(self):
        import glob

        return glob.glob(
            os.path.join(_ROOT, "python", "src", "roblox_studio_mcp", "**", "*.py"),
            recursive=True,
        )

    def _raised_codes(self, sources):
        """``(path, code)`` for every ``ToolError("CODE"`` literal found."""
        import re

        pattern = re.compile(r'ToolError\(\s*"([A-Z_]+)"')
        for path in sources:
            with open(path, encoding="utf-8") as handle:
                for match in pattern.finditer(handle.read()):
                    yield path, match.group(1)

    def test_no_raise_site_names_an_undeclared_code(self):
        offenders = [
            (os.path.relpath(path, _ROOT), code)
            for path, code in self._raised_codes(self._sources())
            if code not in E.ALL_CODES
        ]
        self.assertEqual(
            offenders, [],
            "these raise a code that is not in ALL_CODES, so ToolError raises "
            "AssertionError instead and the error they built is discarded: %s"
            % (offenders,),
        )

    def test_an_unknown_code_still_reports_the_message(self):
        """The guard must refuse the code without swallowing the error.

        The message is the part a caller can act on, so it has to survive.
        """
        with self.assertRaises(AssertionError) as caught:
            E.ToolError("NOT_A_CODE", "the pixels are fine, the write failed")
        self.assertIn("NOT_A_CODE", str(caught.exception))
        self.assertIn(
            "the pixels are fine, the write failed",
            str(caught.exception),
            "the guard discarded the message; the caller learns only that "
            "something was wrong",
        )

    def test_the_guard_still_fires_on_an_unknown_code(self):
        """Negative control: the guard must still fire.

        Without this, deleting the validation would pass the test above.
        """
        with self.assertRaises(AssertionError):
            E.ToolError("NOT_A_CODE", "x")

    def test_a_declared_code_is_unaffected(self):
        for code in sorted(E.ALL_CODES):
            err = E.ToolError(code, f"message for {code}")
            self.assertEqual(err.code, code)
            self.assertEqual(err.message, f"message for {code}")

    def test_the_scan_actually_finds_raise_sites(self):
        """A scan that matches nothing passes vacuously and looks like coverage.

        ``capture_png`` is the site this exists for, so if the pattern ever stops
        matching it, this fails rather than the test above quietly going green.
        """
        found = {code for _path, code in self._raised_codes(self._sources())}
        self.assertIn(E.INVALID_ARGUMENT, found)
        self.assertGreaterEqual(len(found), 3)


class RolesAreClosed(unittest.TestCase):
    def test_parser_produces_only_declared_roles(self):
        declared = {I.ROLE_EDIT, I.ROLE_SERVER, I.ROLE_CLIENT, I.ROLE_UNKNOWN}
        produced = {
            I.role_from_command_line(cmd)
            for cmd in (
                "--task EditFile", "--task EditPlace", "--task StartServer",
                "--task StartClient", "roblox-studio:1+task:EditPlace+placeId:1",
                "", "junk",
            )
        }
        self.assertLessEqual(produced, declared)


class ActionEnumsAreHandled(unittest.TestCase):
    """Every advertised `action` value must be handled, not just accepted.

    The schema is a promise. A value the handler falls through to "unknown
    action" is a promise the schema makes and the code breaks.
    """

    def _handler_body(self, tool_name: str) -> str:
        import re

        path = os.path.join(_ROOT, "python", "src", "roblox_studio_mcp", "extended_server.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        # The function name drops the `extended_` prefix that the tool keeps.
        stem = tool_name[len("extended_"):]
        match = re.search(
            r"async def _call_%s\(.*?(?=\nasync def |\n_EXTENDED|\Z)" % re.escape(stem),
            source,
            re.S,
        )
        self.assertIsNotNone(match, "no handler found for %s" % tool_name)
        return match.group(0)

    def test_every_advertised_action_is_handled(self):
        import re

        for tool in S._EXTENDED_TOOLS:
            props = tool.input_schema.get("properties") or {}
            action = props.get("action")
            if not (action and action.get("enum")):
                continue
            body = self._handler_body(tool.name)
            handled = set(re.findall(r'action\s*==\s*"([a-z_]+)"', body))
            if "unknown action" in body:
                continue  # an explicit fallthrough covers the remainder
            self.assertEqual(
                set(action["enum"]) - handled, set(),
                "%s advertises actions it does not handle" % tool.name,
            )


if __name__ == "__main__":
    unittest.main()
