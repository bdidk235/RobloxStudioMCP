"""The read-only classification must stay honest.

``annotations.readOnlyHint`` is a promise to the client: this tool does not change
anything, so it may run in parallel with others. A wrong promise is worse than no
promise, because the client acts on it. So the set is checked against the handlers
it describes, and every tool that writes or executes is required to be absent.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp import extended_server as server  # noqa: E402
from roblox_studio_mcp.types import Tool  # noqa: E402


class ReadOnlyClassification(unittest.TestCase):
    def test_every_named_tool_exists(self):
        """A rename that only half landed would otherwise leave a name here that
        silently matches nothing, and the tool it was meant to describe loses its
        hint with no error anywhere."""
        known = {t.name for t in server._EXTENDED_TOOLS}
        self.assertEqual(server._READONLY_TOOLS - known, set())

    def test_every_tool_carries_a_classification(self):
        """Not an assertion that the answer is right, only that it is explicit.
        ``read_only`` defaults to False, so a new tool is mutating by default."""
        for tool in server._EXTENDED_TOOLS:
            self.assertIsInstance(tool.read_only, bool)
            self.assertEqual(
                tool.read_only, tool.name in server._READONLY_TOOLS, tool.name
            )

    def test_writing_tools_are_not_marked_read_only(self):
        """Each of these changes the DataModel, the filesystem, or the set of
        running processes. A parallel dispatch on any of them is a data race the
        caller did not ask for."""
        writes = {
            "extended_write_script",
            "extended_update_script",
            "extended_insert_asset_from_file",
            "extended_clear_breakpoints",
            "extended_run_tests",
            "extended_execute_luau_from_file",
            "extended_capture",
        }
        for name in writes:
            tool = next(t for t in server._EXTENDED_TOOLS if t.name == name)
            self.assertFalse(tool.read_only, "%s must not claim read-only" % name)

    def test_ambiguous_tools_are_deliberately_unmarked(self):
        """Two tools that are read-only in one mode and not in another.

        ``extended_wait_for`` runs a caller-supplied probe, so it executes
        whatever the probe executes. ``extended_manage_instance`` lists on one
        action and launches or stops on others, and a single hint cannot describe
        both. Marking either would be a guess, and the safe direction to guess in
        is the restrictive one.
        """
        for name in ("extended_wait_for", "extended_manage_instance"):
            tool = next(t for t in server._EXTENDED_TOOLS if t.name == name)
            self.assertFalse(tool.read_only, "%s should stay unmarked" % name)

    def test_breakpoints_are_not_read_only(self):
        """Setting a breakpoint is a mutation of the debug session even though it
        is not a mutation of the DataModel, and a logpoint is a standing
        instruction to the engine."""
        tool = next(t for t in server._EXTENDED_TOOLS if t.name == "extended_breakpoints")
        self.assertFalse(tool.read_only)

    def test_serialised_form_omits_the_key_when_not_read_only(self):
        """Sending ``readOnlyHint: false`` everywhere is noise. The key appears only
        when it carries the affirmative claim."""
        plain = Tool(name="x", description="d").to_dict()
        self.assertNotIn("annotations", plain)
        hinted = Tool(name="x", description="d", read_only=True).to_dict()
        self.assertEqual(hinted["annotations"], {"readOnlyHint": True})

    def test_round_trips_through_from_dict(self):
        tool = Tool.from_dict({
            "name": "x", "description": "d",
            "inputSchema": {}, "annotations": {"readOnlyHint": True},
        })
        self.assertTrue(tool.read_only)
        self.assertFalse(Tool.from_dict({"name": "y"}).read_only)


if __name__ == "__main__":
    unittest.main()
