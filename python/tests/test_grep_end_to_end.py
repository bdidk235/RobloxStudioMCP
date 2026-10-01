"""End-to-end `extended_script_grep` over a *simulated* relay, no Studio needed.

Why simulated rather than mocked-at-the-parser: the bug had two parts, and only
one of them was in the parser. The other was in the enrichment loop, where a
`list[str]` reached `hit.get("path")`. Testing `_parse_grep_raw` alone would have
caught the silent-empty half and missed the crash.

So this drives the real `extended_script_grep` with a fake Studio that returns
the **captured live reply** and a real script body, and asserts the full output
shape.

Live verification still needs an MCP server restart, because the running process
holds the old module. Recorded rather than implied: these tests prove the code is
correct, not that the deployed server is running it.
"""

import asyncio
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp.extended.grep import extended_script_grep  # noqa: E402

# Captured from a live `script_grep(query="print")` on Studio 0_186489.
LIVE_TEXT_REPLY = (
    'Path: game.ServerScriptService.UsabilityProbe | Line: 2 | print(42)\n'
    'Path: game.ServerScriptService.Helper | Line: 10 | print("x")\n'
    '... Search stopped after reaching the limit of 50 matches.'
)

# What a bare JSON reply looks like, for the case that must keep working.
JSON_ARRAY_REPLY = '[{"path": "game.ServerScriptService.A", "line_number": 4, "content": "print(1)"}]'


class _Result:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text

    def json(self):
        import json as _json

        return _json.loads(self._text)


class _Studio:
    """Minimal Studio stand-in. Records calls so the tests can assert the
    ``script_read`` fallback happened exactly once per hit, and not at all when
    the relay already supplied content."""

    def __init__(self, reply, source="print(42)\nlocal x = 1\nprint(x)\n"):
        self.reply = reply
        self.source = source
        self.reads = []

    async def call(self, tool, kwargs):
        assert tool == "script_grep", tool
        return _Result(self.reply)

    async def script_read(self, target_file):
        self.reads.append(target_file)

        class R:
            def text(_self):
                return self.source

        return R()


def run(reply, **kwargs):
    studio = _Studio(reply)
    hits = asyncio.run(extended_script_grep(studio, "print", **kwargs))
    return hits, studio


class LiveTextReply(unittest.TestCase):
    def test_it_returns_hits_rather_than_an_empty_list(self):
        """The regression. This exact reply produced ``[]`` with a success status
        and, with ``root_path``, an unhandled ``AttributeError``."""
        hits, _ = run(LIVE_TEXT_REPLY)
        self.assertEqual(len(hits), 2, hits)

    def test_paths_and_line_numbers_come_through(self):
        hits, _ = run(LIVE_TEXT_REPLY)
        self.assertEqual(hits[0]["path"], "game.ServerScriptService.UsabilityProbe")
        self.assertEqual(hits[0]["line_number"], 2)
        self.assertEqual(hits[1]["line_number"], 10)

    def test_the_truncation_notice_is_not_reported_as_a_hit(self):
        hits, _ = run(LIVE_TEXT_REPLY)
        self.assertFalse(any("Search stopped" in h["path"] for h in hits))

    def test_every_hit_has_exactly_the_four_output_keys(self):
        hits, _ = run(LIVE_TEXT_REPLY)
        for hit in hits:
            self.assertEqual(
                set(hit), {"path", "line_number", "excerpt", "context_lines"}, hit
            )

    def test_content_from_the_reply_is_used_and_no_read_is_needed(self):
        """The relay already sent the matching line, so a source read is wasted
        work and a wasted turn."""
        hits, studio = run(LIVE_TEXT_REPLY)
        self.assertIn("print(42)", hits[0]["excerpt"])
        self.assertEqual(studio.reads, [], "should not read when content was supplied")


class NoCrashOnAnyShape(unittest.TestCase):
    """The second defect: a non-dict must never reach ``hit.get``."""

    def test_a_json_array_of_strings_does_not_crash(self):
        """This is the exact input that produced
        ``'str' object has no attribute 'get'``."""
        hits, _ = run('["not", "a", "dict"]')
        self.assertEqual(hits, [])

    def test_mixed_junk_is_dropped_and_real_hits_survive(self):
        reply = '["junk", {"path": "game.S", "line_number": 2, "content": "print(1)"}]'
        hits, _ = run(reply)
        self.assertEqual([h["path"] for h in hits], ["game.S"])

    def test_unparseable_reply_is_empty_not_an_exception(self):
        hits, _ = run("something unexpected entirely")
        self.assertEqual(hits, [])


class JsonStillWorks(unittest.TestCase):
    def test_a_json_array_reply_is_unchanged(self):
        """The new text branch must not shadow the formats that already worked."""
        hits, _ = run(JSON_ARRAY_REPLY)
        self.assertEqual([h["path"] for h in hits], ["game.ServerScriptService.A"])
        self.assertEqual(hits[0]["line_number"], 4)

    def test_source_is_read_when_the_reply_omits_content(self):
        """The enrichment fallback, which is the whole point of the tool over raw
        ``script_grep``: context lines the relay did not send."""
        reply = '[{"path": "game.ServerScriptService.A", "line_number": 2}]'
        hits, studio = run(reply, context_lines=1)
        self.assertEqual(studio.reads, ["game.ServerScriptService.A"])
        self.assertEqual(hits[0]["context_lines"], 1)
        # context_lines=1 is one line either side, so line 2 of a 3-line source
        # yields all three. An earlier version of this test expected two lines and
        # was wrong - `context_lines` is a radius, not a total.
        self.assertEqual(hits[0]["excerpt"], "print(42)\nlocal x = 1\nprint(x)")


if __name__ == "__main__":
    unittest.main()
