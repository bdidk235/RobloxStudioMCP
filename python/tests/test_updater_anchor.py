"""Anchor matching for ``update_script``.

Three passes: exact, prefix-stripped, rstrip. Each is tested from the side that
can fail independently - a query that only the fallback can find, a query that
neither the exact nor the prefix pass can find, and the ambiguity case where a
fallback must refuse rather than pick.
"""

from __future__ import annotations

import unittest

from roblox_studio_mcp.extended.errors import INVALID_ARGUMENT, ToolError
from roblox_studio_mcp.extended.updater import _locate, update_script
from roblox_studio_mcp.extended.writer import _strip_line_prefixes


class _Text:
    def __init__(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


class FakeStudio:
    """Returns ``NN→`` numbered lines the way ``script_read`` does."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.calls = []

    @property
    def studio_id(self):
        return None

    async def script_read(self, target_file=None, **kwargs):
        numbered = "\n".join(
            f"{i + 1}→{line}" for i, line in enumerate(self.source.split("\n"))
        )
        return _Text(numbered)

    async def call(self, name, arguments=None):
        self.calls.append((name, arguments))
        return {}

    async def execute_luau(self, code, **kwargs):
        self.calls.append(("execute_luau", code))
        return {}


class TestExactAnchor(unittest.TestCase):
    def test_span_slices_back_to_the_query(self):
        source = "local a = 1\nlocal b = 2"
        spans, strategy = _locate(source, "local b = 2")
        self.assertEqual(strategy, "exact")
        self.assertEqual(source[spans[0][0]:spans[0][1]], "local b = 2")


class TestPrefixAnchor(unittest.IsolatedAsyncioTestCase):
    async def test_query_pasted_from_script_read_is_accepted(self):
        studio = FakeStudio("local a = 1\nlocal b = 2")
        result = await update_script(
            studio, "game.S.A", [("2→local b = 2", "local b = 3")]
        )
        self.assertEqual(result.updated, [0])
        self.assertEqual(result.matched_by, {0: "prefix-stripped"})
        self.assertTrue(any("prefix-stripped" in w for w in result.warnings))


class TestTrailingWhitespaceAnchor(unittest.IsolatedAsyncioTestCase):
    async def test_query_carries_spaces_the_file_does_not(self):
        studio = FakeStudio("local a = 1\nlocal b = 2")
        result = await update_script(
            studio, "game.S.A", [("local a = 1   ", "local a = 9")]
        )
        self.assertEqual(result.updated, [0])
        self.assertEqual(result.matched_by, {0: "rstrip"})
        self.assertTrue(any("line 1" in w for w in result.warnings))

    async def test_the_other_direction_needs_no_fallback(self):
        # File has the trailing spaces and the query does not: that is already an
        # exact substring match, so `rstrip` is not what saves it. Pinned here
        # because the asymmetry is easy to misread as rstrip being unnecessary.
        studio = FakeStudio("local a = 1   \nlocal b = 2")
        result = await update_script(studio, "game.S.A", [("local a = 1", "local a = 9")])
        self.assertEqual(result.updated, [0])
        self.assertEqual(result.matched_by, {0: "exact"})

    async def test_two_candidate_blocks_are_ambiguous_not_first_wins(self):
        studio = FakeStudio("ab \nab")
        result = await update_script(studio, "game.S.A", [("ab   ", "cd")])
        self.assertEqual(result.skipped_ambiguous, [0])
        self.assertEqual(result.updated, [])
        self.assertNotIn(0, result.matched_by)


class TestStrict(unittest.IsolatedAsyncioTestCase):
    async def test_strict_refuses_a_fallback(self):
        studio = FakeStudio("local a = 1")
        with self.assertRaises(ToolError) as caught:
            await update_script(
                studio, "game.S.A", [("local a = 1   ", "local a = 2")], strict=True
            )
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertIn("rstrip", str(caught.exception))

    async def test_strict_still_applies_an_exact_match(self):
        studio = FakeStudio("local a = 1")
        result = await update_script(
            studio, "game.S.A", [("local a = 1", "local a = 2")], strict=True
        )
        self.assertEqual(result.updated, [0])
        self.assertEqual(result.matched_by, {0: "exact"})


class TestStripLinePrefixes(unittest.TestCase):
    def test_trailing_newline_survives(self):
        self.assertTrue(_strip_line_prefixes("  1→a\n  2→b\n").endswith("b\n"))

    def test_without_trailing_newline_is_unchanged_in_shape(self):
        self.assertEqual(_strip_line_prefixes("  1→a\n  2→b"), "a\nb")
