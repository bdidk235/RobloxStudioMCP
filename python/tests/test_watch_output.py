"""`extended_watch_output` behaviour, which had no test at all.

The only prior coverage asserted the tool's *description*. That is how a handler
which returned `{"returned": 0}` on every call, with `isError: false`, survived:
the surface looked right and nothing ever checked the payload.

The bug: the handler read ``result["text"]``, but ``watch_output`` returns a dict
keyed ``new_lines`` / ``total_lines`` / ``last_line``. ``.get`` supplied the
default, the split produced nothing, and every call reported an empty result as
a success. Live confirmation: ``total_seen: 0`` against a live Studio.

These tests pin the payload, not the prose. The filter and cap are also the only
protection against a bulk console dump, and the rsx-breakpoints skill depends on
``pattern`` to read hits, so both are behaviour, not decoration.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp import extended_server as es  # noqa: E402

CONSOLE = [
    "MARKER alpha hit i=1",
    "noise one",
    "MARKER beta hit i=2",
    "noise two",
    "MARKER gamma hit i=3",
]


def _watch_returning(lines):
    """Stand in for `watch_output`, returning the real watch's shape."""
    return {"new_lines": list(lines), "total_lines": len(lines), "last_line": lines[-1] if lines else None}


class WatchOutput(unittest.TestCase):
    def _call(self, watch_result, **arguments):
        arguments.setdefault("studio_id", "studio-1")
        with mock.patch.object(es, "_RobloxStudio", mock.Mock()), \
             mock.patch("roblox_studio_mcp.extended.extensions.watch_output",
                        new=mock.AsyncMock(return_value=watch_result)), \
             mock.patch("roblox_studio_mcp.extended.extensions.get_watch_state",
                        new=mock.Mock(return_value=mock.Mock())):
            return asyncio.run(es._call_watch_output(mock.Mock(), arguments))

    @staticmethod
    def _payload(result):
        import json
        return json.loads(result["content"][0]["text"])

    # -- the regression ----------------------------------------------------- #

    def test_lines_from_the_watch_are_returned(self):
        """The exact bug: the payload used to be empty on every call."""
        got = self._payload(self._call(_watch_returning(CONSOLE)))
        self.assertEqual(got["total_seen"], len(CONSOLE))
        self.assertEqual(got["returned"], len(CONSOLE))
        self.assertTrue(got["lines"])

    def test_it_is_not_an_error_when_there_is_output(self):
        result = self._call(_watch_returning(CONSOLE))
        self.assertFalse(result["isError"])

    def test_new_lines_is_the_key_that_exists(self):
        """Pins the shape, so a rename upstream fails here rather than silently
        returning nothing again."""
        result = self._payload(self._call({"new_lines": ["one"], "total_lines": 1,
                                           "last_line": "one"}))
        self.assertEqual(result["lines"], ["one"])

    def test_an_unknown_shape_still_degrades_rather_than_raising(self):
        """If the watch's shape changes again, the fallback must not throw."""
        result = self._payload(self._call({"text": "a\\nb\\nc"}))
        self.assertEqual(result["total_seen"], 3)

    # -- filter ------------------------------------------------------------- #

    def test_pattern_filters(self):
        got = self._payload(self._call(_watch_returning(CONSOLE), pattern="^MARKER"))
        self.assertEqual(got["matched"], 3)
        self.assertEqual(got["returned"], 3)
        self.assertNotIn("noise one", got["text"])

    def test_pattern_that_matches_nothing_is_empty_not_an_error(self):
        got = self._payload(self._call(_watch_returning(CONSOLE), pattern="^NOPE"))
        self.assertEqual(got["matched"], 0)
        self.assertEqual(got["returned"], 0)

    def test_a_bad_pattern_is_rejected_with_a_code(self):
        """This is the case the breakpoints skill depends on: `pattern` is how a
        hit is read out of a noisy console, so a silently-ignored pattern would
        look like "no breakpoint was hit"."""
        with self.assertRaises(es.ToolError) as caught:
            self._call(_watch_returning(CONSOLE), pattern="([unclosed")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    # -- cap ---------------------------------------------------------------- #

    def test_max_lines_caps_and_keeps_the_newest(self):
        got = self._payload(self._call(_watch_returning(CONSOLE), max_lines=2))
        self.assertEqual(got["returned"], 2)
        self.assertEqual(got["matched"], 5)
        self.assertTrue(got["truncated"])
        self.assertIn("MARKER gamma", got["text"])
        self.assertNotIn("MARKER alpha", got["text"])

    def test_the_cap_applies_after_filtering(self):
        got = self._payload(self._call(_watch_returning(CONSOLE), pattern="^MARKER",
                                       max_lines=1))
        self.assertEqual(got["matched"], 3)
        self.assertEqual(got["returned"], 1)
        self.assertIn("MARKER gamma", got["text"])

    def test_max_lines_below_one_is_rejected(self):
        for bad in (0, -5):
            with self.assertRaises(es.ToolError) as caught:
                self._call(_watch_returning(CONSOLE), max_lines=bad)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_a_non_integer_cap_is_rejected(self):
        for bad in ("lots", [3], {"n": 3}, object()):
            with self.assertRaises(es.ToolError) as caught:
                self._call(_watch_returning(CONSOLE), max_lines=bad)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_a_numeric_string_cap_is_accepted(self):
        """`"50"` is a plausible thing for a caller to send, and the schema says
        integer, so coercing it is friendlier than failing. Pinned so the
        behaviour is a decision rather than an accident."""
        got = self._payload(self._call(_watch_returning(CONSOLE), max_lines="2"))
        self.assertEqual(got["returned"], 2)

    def test_a_fractional_cap_truncates_rather_than_failing(self):
        """Also pinned, because it is surprising enough to be worth a test: 1.5
        becomes 1, not an error. Rejection was considered and not chosen, since
        a cap is a ceiling and rounding it down cannot return too much."""
        got = self._payload(self._call(_watch_returning(CONSOLE), max_lines=1.5))
        self.assertEqual(got["returned"], 1)

    def test_zero_is_not_silently_the_default(self):
        """`0 or 200` used to swallow this: 0 is falsy, so a caller asking for
        no lines got 200 - a default in disguise, and a bulk-data hazard."""
        with self.assertRaises(es.ToolError):
            self._call(_watch_returning(CONSOLE), max_lines=0)

    def test_an_omitted_cap_takes_the_default(self):
        """The other half: absent really is absent, and means 200."""
        many = ["line %d" % i for i in range(500)]
        got = self._payload(self._call(_watch_returning(many), studio_id="studio-1"))
        self.assertEqual(got["returned"], 200)

    def test_the_default_cap_is_200(self):
        many = ["line %d" % i for i in range(500)]
        got = self._payload(self._call(_watch_returning(many)))
        self.assertEqual(got["matched"], 500)
        self.assertEqual(got["returned"], 200)
        self.assertTrue(got["truncated"])


class PlacePathSpaces(unittest.TestCase):
    """`place_from_command_line` had the same `(\\S+)` bug as the log parser.

    Harmless for this machine's own paths (they live under `%TEMP%`, no spaces)
    and silently wrong for any place in a directory that has one. A truncated path
    still yields a basename - a fragment - so it simply never matched a mesh name
    rather than raising. Found while porting the function to Node, where the
    mirrored test caught the port immediately.
    """

    def test_a_path_under_temp_still_parses(self):
        from roblox_studio_mcp.extended.instance import place_from_command_line

        cmd = (
            r"C:\Roblox\Versions\v1\RobloxStudioBeta.exe --task EditFile "
            r"--localPlaceFile C:\Users\User\AppData\Local\Temp\baseplates\Baseplate-1.rbxl"
        )
        self.assertEqual(place_from_command_line(cmd), "Baseplate-1.rbxl")

    def test_a_path_with_spaces_parses_whole(self):
        from roblox_studio_mcp.extended.instance import place_from_command_line

        cmd = (
            r"C:\Roblox\Versions\v1\RobloxStudioBeta.exe --task EditFile "
            r"--localPlaceFile C:\Users\User\My Places\Baseplate-1.rbxl"
        )
        self.assertEqual(place_from_command_line(cmd), "Baseplate-1.rbxl")

    def test_a_quoted_path_with_spaces_parses(self):
        from roblox_studio_mcp.extended.instance import place_from_command_line

        cmd = (
            'RobloxStudioBeta.exe --localPlaceFile "C:\\a dir\\b\\Place-9.rbxl" -task EditFile'
        )
        self.assertEqual(place_from_command_line(cmd), "Place-9.rbxl")

    def test_a_play_test_project_file_with_spaces_parses(self):
        from roblox_studio_mcp.extended.instance import place_from_command_line

        cmd = (
            "RobloxStudioBeta.exe -task StartServer "
            "-localProjectFile C:\\Users\\User\\My Places\\Baseplate-1.rbxl"
        )
        self.assertEqual(place_from_command_line(cmd), "Baseplate-1.rbxl")

    def test_a_uri_launch_has_no_derivable_path(self):
        from roblox_studio_mcp.extended.instance import place_from_command_line

        self.assertIsNone(
            place_from_command_line("RobloxStudioBeta.exe roblox-studio:1+task:EditPlace+placeId:1")
        )


class SurfaceParity(unittest.TestCase):
    """The Node schema must accept what the Python schema accepts.

    Found by measurement: Node's `extended_watch_output` had only `studio_id`, so
    the filter and the cap existed on one side alone. Because unknown parameters
    are silently ignored (request P0.2a), passing `pattern` on the Node side did
    not fail - it returned the whole buffer, which reads exactly like "no hits".
    """

    def _props(self, tool_name):
        for tool in es._EXTENDED_TOOLS:
            if tool.name == tool_name:
                return set(tool.input_schema.get("properties", {}))
        self.fail("no such tool: %s" % tool_name)

    def test_watch_output_advertises_the_filter_and_the_cap(self):
        props = self._props("extended_watch_output")
        self.assertIn("pattern", props)
        self.assertIn("max_lines", props)


if __name__ == "__main__":
    unittest.main()
