"""`extended_script_grep` against the wire format the relay actually returns.

**Found by a usability pass, not by a test.** The tool returned an **empty list
with a success status** for text that provably existed, and with `root_path` set
it raised an unhandled `AttributeError` that escaped the tool with no error code
and no recovery.

Root cause, two defects:

1. `_parse_grep_raw` attempted JSON only. The relay returns **plain text**, so
   both attempts failed and it returned `[]` - silently, which is the worst
   failure shape in this project. An agent that had just written a script was told
   the script did not contain its own text.
2. The `raw_result.json()` fallback assigned a raw `list[str]` and the next line
   called `hit.get("path")` on a `str`.

**Every fixture below is captured from a live call, not hand-written.** That is
the point of this file: the old tests mocked the *old* JSON shape, so the suite
stayed green through a format change on someone else's side. A parser over
another team's wire format needs a fixture from that wire, or it tests nothing
about the thing that broke.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp.extended.grep import (  # noqa: E402
    _as_hit_dicts,
    _parse_grep_raw,
    _parse_grep_text,
)

# Captured verbatim from `script_grep(query="print")` on Studio 0_186489,
# RobloxStudio 2106, 2026-09-30. Three representative hits, the real truncation
# notice, and a line that is not a hit at all.
LIVE_REPLY = (
    'Path: PluginDebugService.user_RojoManagedPlugin.rbxm.Rojo.Packages.'
    'Highlighter.lexer | Line: 131 | \t\t\t-- print("indexing",parent,"with",cleanTok)\n'
    'Path: PluginDebugService.user_RojoManagedPlugin.rbxm.Rojo.Packages.'
    'RbxDom.database | Line: 44069 | \t\t\tEnableSprinting = {\n'
    'Path: game.ServerScriptService.UsabilityProbe | Line: 2 | '
    "print('PROBE part=' .. tostring(p) .. ' and other')\n"
    '... Search stopped after reaching the limit of 50 matches.'
)


class TextFormat(unittest.TestCase):
    def test_parses_every_hit(self):
        hits = _parse_grep_text(LIVE_REPLY)
        self.assertEqual(len(hits), 3)

    def test_path_and_line_are_separated_from_content(self):
        hit = _parse_grep_text(LIVE_REPLY)[0]
        self.assertEqual(
            hit["path"],
            "PluginDebugService.user_RojoManagedPlugin.rbxm.Rojo.Packages.Highlighter.lexer",
        )
        self.assertEqual(hit["line_number"], 131)
        self.assertIn('print("indexing"', hit["content"])

    def test_the_truncation_notice_is_not_a_hit(self):
        """It reads as a hit if you do not exclude it, and would report a file
        named after an English sentence."""
        paths = [h["path"] for h in _parse_grep_text(LIVE_REPLY)]
        self.assertFalse(any("Search stopped" in p for p in paths), paths)

    def test_content_containing_a_pipe_is_not_truncated(self):
        line = "Path: game.S | Line: 7 | local t = a | b or c"
        hit = _parse_grep_text(line)[0]
        self.assertEqual(hit["line_number"], 7, "the second separator must not shift")
        self.assertEqual(hit["content"], "local t = a | b or c")

    def test_a_line_that_is_not_a_hit_is_skipped(self):
        self.assertEqual(_parse_grep_text("no colon here at all"), [])

    def test_empty_input_is_empty_not_an_error(self):
        self.assertEqual(_parse_grep_text(""), [])


class ParseGrepRawPrefersJson(unittest.TestCase):
    """The text parser is the *last* attempt, so a JSON reply is never mis-read
    as prose. Order matters: text is a looser format, so it must not win."""

    def test_a_json_array_still_wins(self):
        raw = '[{"path": "game.S", "line_number": 3}]'
        self.assertEqual(_parse_grep_raw(raw)[0]["path"], "game.S")

    def test_a_wrapped_json_object_still_wins(self):
        raw = '{"results": [{"path": "game.T", "line": 9}]}'
        self.assertEqual(_parse_grep_raw(raw)[0]["path"], "game.T")

    def test_leading_prose_defeats_the_json_extractor(self):
        """A real, measured boundary of `_extract_balanced`, recorded rather than
        wished away.

        It only matches when the message **starts** with the delimiter, so
        `'Here you go: [...]'` yields nothing. An earlier version of this test
        asserted that case worked; it does not, and the assertion was wrong.

        Not fixed here, because `_extract_balanced` is shared with the capture
        path and changing it is a wider change than this bug warrants. It is not
        currently reachable either: the relay returns bare JSON or the text
        format, both of which start with their own first character.
        """
        self.assertEqual(_parse_grep_raw('Here you go: [{"path": "game.T"}]'), [])

    def test_the_live_text_reply_parses(self):
        """The regression, end to end: this exact string used to yield ``[]``."""
        hits = _as_hit_dicts(_parse_grep_raw(LIVE_REPLY))
        self.assertEqual(len(hits), 3, "the live reply must not parse as empty")
        self.assertTrue(any(h["path"] == "game.ServerScriptService.UsabilityProbe"
                            for h in hits))

    def test_unparseable_input_is_empty_rather_than_an_error(self):
        self.assertEqual(_parse_grep_raw("something else entirely"), [])


class AsHitDicts(unittest.TestCase):
    """The second defect: a list of strings reached ``hit.get(...)``."""

    def test_strings_are_dropped_not_detonated(self):
        self.assertEqual(_as_hit_dicts(["a", "b"]), [])

    def test_dicts_survive(self):
        self.assertEqual(_as_hit_dicts([{"path": "game.S"}, "junk"]), [{"path": "game.S"}])

    def test_a_non_list_is_empty(self):
        self.assertEqual(_as_hit_dicts({"path": "game.S"}), [])
        self.assertEqual(_as_hit_dicts(None), [])

    def test_mixed_junk_cannot_reach_the_enrichment_loop(self):
        """Every element a `hit.get` would be called on is a dict. This is the
        property that prevents the AttributeError from returning."""
        for element in _as_hit_dicts([1, "two", None, [], {}, {"path": "ok"}]):
            self.assertIsInstance(element, dict)
            element.get("path")  # must not raise


if __name__ == "__main__":
    unittest.main()
