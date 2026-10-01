"""Tests for the breakpoint registry and the console hit-parsing contract."""

import unittest

from roblox_studio_mcp.errors import MCPToolError
from roblox_studio_mcp.extended.errors import ToolError
from roblox_studio_mcp.extended.breakpoints import (
    HIT_PREFIX,
    list_breakpoints,
    set_breakpoint,
)
from roblox_studio_mcp.types import CallToolResult


class FakeStudio:
    """Answers execute_luau with a canned header and records what it was sent."""

    def __init__(self, response="ok"):
        self.response = response
        self.sent: list[dict] = []

    async def call(self, name, arguments=None):
        arguments = arguments or {}
        self.sent.append({"tool": name, **arguments})
        return CallToolResult.from_dict({"content": [{"type": "text", "text": self.response}]})


class TestHeaderParsing(unittest.IsolatedAsyncioTestCase):
    async def test_empty_list(self):
        studio = FakeStudio("count=0 ")
        self.assertEqual(await list_breakpoints(studio), [])

    async def test_one_entry(self):
        studio = FakeStudio('count=1 BpTest_L5|BpTest:5 = error("hit")')
        got = await list_breakpoints(studio)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["script_path"], "BpTest")
        self.assertEqual(got[0]["line"], 5)
        self.assertEqual(got[0]["log_expression"], 'error("hit")')

    async def test_several_entries_sorted_by_luau(self):
        studio = FakeStudio(
            'count=2 A_L1|A:1 = error("a") | B_L2|B:2 = error("b")'
        )
        got = await list_breakpoints(studio)
        self.assertEqual([b["script_path"] for b in got], ["A", "B"])
        self.assertEqual([b["line"] for b in got], [1, 2])


class TestSetBreakpoint(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_a_non_positive_line(self):
        studio = FakeStudio()
        for bad in (0, -1):
            with self.assertRaises(ToolError) as caught:
                await set_breakpoint(studio, "BpTest", bad)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    async def test_defaults_to_a_failing_expression(self):
        # A successful log injection is invisible through the console, so the
        # default must fail deliberately to be the reporting channel.
        studio = FakeStudio("ok")
        await set_breakpoint(studio, "BpTest", 5)
        assert "error(" in studio.sent[0]["code"]
        assert "ContinueExecution = true" in studio.sent[0]["code"]

    async def test_targets_the_server_datamodel(self):
        studio = FakeStudio("ok")
        await set_breakpoint(studio, "BpTest", 5)
        self.assertEqual(studio.sent[0]["datamodel_type"], "Server")

    async def test_raises_when_studio_refuses(self):
        for status in ("no-such-script:BpTest", "unverified:", "error:boom"):
            with self.subTest(status=status):
                studio = FakeStudio(status)
                with self.assertRaises(RuntimeError):
                    await set_breakpoint(studio, "BpTest", 5)


class TestHitContract(unittest.TestCase):
    """The console arrives as ONE line with literal \\n escapes."""

    def test_hit_prefix_is_the_engine_format(self):
        self.assertEqual(HIT_PREFIX, "Breakpoint ")

    def test_hits_are_extractable_by_pattern(self):
        import re

        # this is the shape get_console_output actually returns
        console = (
            '"BPX running\\nBreakpoint ServerScriptService.BpTest:5 ignored: '
            '[string \\"logExpression\\"]:1: HIT i=1\\nTICK 1\\n'
            'Breakpoint ServerScriptService.BpTest:5 ignored: '
            '[string \\"logExpression\\"]:1: HIT i=2\\nBPX done"'
        )
        found = [int(n) for n in re.findall(r"HIT i=(\d+)", console)]
        self.assertEqual(found, [1, 2])

    def test_a_bare_splitlines_misses_them(self):
        # the trap: this looks like one line, so per-line scanning for a
        # substring still works but per-line parsing of digits does not
        console = '"A\\nHIT i=7\\nB"'
        self.assertEqual(len(console.splitlines()), 1)
        self.assertIn("HIT i=7", console)


if __name__ == "__main__":
    unittest.main()
