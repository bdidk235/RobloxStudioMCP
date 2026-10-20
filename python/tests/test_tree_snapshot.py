"""Tests for the bounded tree snapshot.

Three things are pinned here, in the order they can hurt.

**The path.** It is spliced into generated Luau, so a path that could carry
code must be refused *before* it reaches the generator. The shared validator
(``writer.validate_game_tree_path``) is regex-only for exactly this reason -
it was written after a PoC-measured injection in the writer's own path
handling.

**The bounds.** The ``execute_luau`` return channel truncates at exactly
100,015 characters with no error, so a snapshot that lost its tail would
read as a complete tree. The trailer line is what makes the truncation
detectable, and these tests check that a missing trailer is an error rather
than a partial answer.

**The surface.** The tool is advertised, read-only, and its description is
inside the cap - because the description is paid on every call of every
session, and a tool added without that check is a permanent cost.
"""

import asyncio
import json
import os
import unittest

from roblox_studio_mcp import extended_server as es
from roblox_studio_mcp.extended.errors import (
    INVALID_ARGUMENT, LUA_ERROR, NOT_FOUND, SIZE_LIMIT, ToolError,
)
from roblox_studio_mcp.extended.snapshot import (
    DEFAULT_DEPTH, DEFAULT_NODES, MAX_DEPTH, MAX_NODES, bounded_int,
    build_walk, describe_flags, parse_snapshot, snapshot, unescape,
    validate_root_path,
)
from roblox_studio_mcp.types import CallToolResult

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CONTRACT = os.path.join(REPO_ROOT, "contract", "tools.json")


class _FakeStudio:
    """A studio whose ``execute_luau`` returns a scripted reply.

    The code it was given is kept, so a test can assert on what was
    generated - which is the only way to check the splice without a Studio.
    """

    def __init__(self, text):
        self.text = text
        self.calls = []

    async def execute_luau(self, code, datamodel_type="Edit"):
        self.calls.append((code, datamodel_type))
        return CallToolResult(content=[{"type": "text", "text": self.text}])


def _walk_output(nodes, count=None, truncated=False, depth_limited=False):
    """Build the text a successful walk returns."""
    lines = list(nodes)
    lines.append(
        "__END__\t%d\t%s\t%s"
        % (
            len(nodes) if count is None else count,
            "true" if truncated else "false",
            "true" if depth_limited else "false",
        )
    )
    return "\n".join(lines)


class TestPathValidation(unittest.TestCase):
    """The path reaches generated Luau, so it is validated first."""

    def test_the_root_itself_is_allowed(self):
        # The shared validator requires at least one segment after "game";
        # the bare root is a legitimate walk root and is the one case it
        # does not cover.
        self.assertEqual(validate_root_path("game"), "game")

    def test_an_ordinary_path_is_allowed(self):
        self.assertEqual(
            validate_root_path("game.Workspace"), "game.Workspace"
        )
        self.assertEqual(
            validate_root_path("game.Workspace.Part"),
            "game.Workspace.Part",
        )

    def test_a_path_that_is_not_game_rooted_is_refused(self):
        for bad in ("Workspace", "workspace.Part", "", "Players"):
            with self.assertRaises(ToolError) as caught:
                validate_root_path(bad)
            self.assertEqual(caught.exception.code, INVALID_ARGUMENT)

    def test_a_path_carrying_code_is_refused(self):
        # The PoC that made the shared validator: a newline in the path
        # lands caller-controlled lines as executed code in the Editor.
        for bad in (
            "game.Workspace\nprint('PWNED')",
            "game.Workspace\n--.Evil",
            "game.Work space",
            "game.Workspace;print(1)",
        ):
            with self.assertRaises(ToolError) as caught:
                validate_root_path(bad)
            self.assertEqual(caught.exception.code, INVALID_ARGUMENT)

    def test_a_non_string_is_refused(self):
        for bad in (None, 42, ["game"], {"path": "game"}):
            with self.assertRaises(ToolError) as caught:
                validate_root_path(bad)
            self.assertEqual(caught.exception.code, INVALID_ARGUMENT)


class TestBounds(unittest.TestCase):
    """The bounds are enforced host-side, so the generated code is safe."""

    def test_the_defaults_are_used_when_nothing_is_passed(self):
        self.assertEqual(
            bounded_int(None, "max_depth", 1, MAX_DEPTH, DEFAULT_DEPTH),
            DEFAULT_DEPTH,
        )
        self.assertEqual(
            bounded_int(None, "max_nodes", 1, MAX_NODES, DEFAULT_NODES),
            DEFAULT_NODES,
        )

    def test_a_value_out_of_range_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            bounded_int(0, "max_depth", 1, MAX_DEPTH, DEFAULT_DEPTH)
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        with self.assertRaises(ToolError) as caught:
            bounded_int(MAX_NODES + 1, "max_nodes", 1, MAX_NODES, DEFAULT_NODES)
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)

    def test_a_bool_is_refused_rather_than_becoming_an_int(self):
        # True is an int in Python, and int(True) == 1 - a silent one-level
        # walk is a wrong answer, not a validation error.
        with self.assertRaises(ToolError) as caught:
            bounded_int(True, "max_depth", 1, MAX_DEPTH, DEFAULT_DEPTH)
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)

    def test_a_string_is_refused(self):
        # The schema says integer; a string would otherwise be coerced, and
        # "5" would quietly become 5.
        with self.assertRaises(ToolError) as caught:
            bounded_int("5", "max_nodes", 1, MAX_NODES, DEFAULT_NODES)
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)


class TestBuildWalk(unittest.TestCase):
    """The generated code carries the bounds as literals."""

    def test_the_caps_and_the_path_are_spliced_in(self):
        code = build_walk("game.Workspace", 3, 200)
        self.assertIn("local MAX_NODES = 200", code)
        self.assertIn("local MAX_DEPTH = 3", code)
        self.assertIn('resolve("game.Workspace")', code)

    def test_no_placeholder_survives(self):
        code = build_walk("game.Workspace", 4, 50)
        for placeholder in ("__NODES__", "__DEPTH__", "__PATH__"):
            self.assertNotIn(placeholder, code)


class TestParse(unittest.TestCase):
    """The trailer is what makes a truncated return detectable."""

    def test_a_good_reply_parses(self):
        text = _walk_output(
            [
                "game\tDataModel\t3",
                "game.Workspace\tWorkspace\t2",
                "game.Workspace.Part\tPart\t0",
            ]
        )
        nodes, count, truncated, depth_limited = parse_snapshot(text)
        self.assertEqual(count, 3)
        self.assertFalse(truncated)
        self.assertFalse(depth_limited)
        self.assertEqual(nodes[1]["path"], "game.Workspace")
        self.assertEqual(nodes[1]["className"], "Workspace")
        self.assertEqual(nodes[1]["children"], 2)
        self.assertEqual(nodes[2]["path"], "game.Workspace.Part")

    def test_a_missing_trailer_is_a_size_limit_not_a_partial_tree(self):
        # This is the truncation case: the channel cut the reply, so the
        # tree is incomplete and must not be handed back as if it were not.
        text = "game\tDataModel\t3\ngame.Workspace\tWorkspace\t2"
        with self.assertRaises(ToolError) as caught:
            parse_snapshot(text)
        self.assertEqual(caught.exception.code, SIZE_LIMIT)

    def test_a_malformed_node_line_is_a_lua_error(self):
        text = _walk_output(["game\tDataModel\t3", "no-tabs-here"])
        with self.assertRaises(ToolError) as caught:
            parse_snapshot(text)
        self.assertEqual(caught.exception.code, LUA_ERROR)

    def test_a_non_numeric_child_count_is_a_lua_error(self):
        text = _walk_output(["game\tDataModel\tmany"])
        with self.assertRaises(ToolError) as caught:
            parse_snapshot(text)
        self.assertEqual(caught.exception.code, LUA_ERROR)

    def test_escapes_round_trip(self):
        # A tab in an instance name would otherwise break the field
        # separator, so names are escaped on the way out.
        text = _walk_output(["game.Works\\9pace\tFolder\t0"])
        nodes, _, _, _ = parse_snapshot(text)
        self.assertEqual(nodes[0]["path"], "game.Works\tpace")
        self.assertEqual(nodes[0]["className"], "Folder")

    def test_a_backslash_survives_the_round_trip(self):
        self.assertEqual(unescape("a\\\\b"), "a\\b")
        self.assertEqual(unescape("a\\9b"), "a\tb")


class TestSnapshot(unittest.TestCase):
    """The read itself, against a scripted Studio."""

    def test_a_read_returns_nodes_and_the_bounds_applied(self):
        studio = _FakeStudio(
            _walk_output(
                [
                    "game.Workspace\tWorkspace\t1",
                    "game.Workspace.Part\tPart\t0",
                ],
                truncated=False,
                depth_limited=True,
            )
        )
        report = asyncio.run(
            snapshot(studio, path="game.Workspace", max_depth=2, max_nodes=10)
        )
        self.assertEqual(report["root"], "game.Workspace")
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["max_depth"], 2)
        self.assertEqual(report["max_nodes"], 10)
        self.assertFalse(report["truncated"])
        self.assertTrue(report["depth_limited"])
        self.assertEqual(report["nodes"][0]["path"], "game.Workspace")
        self.assertEqual(report["nodes"][1]["path"], "game.Workspace.Part")

    def test_the_walk_is_run_in_the_edit_datamodel(self):
        studio = _FakeStudio(_walk_output(["game\tDataModel\t0"]))
        asyncio.run(snapshot(studio))
        self.assertEqual(studio.calls[0][1], "Edit")

    def test_a_path_that_does_not_resolve_is_not_found(self):
        studio = _FakeStudio("ERR\tNOT_FOUND")
        with self.assertRaises(ToolError) as caught:
            asyncio.run(snapshot(studio, path="game.Nope"))
        self.assertEqual(caught.exception.code, NOT_FOUND)

    def test_a_truncated_reply_is_a_size_limit(self):
        studio = _FakeStudio("game\tDataModel\t1\ngame.Workspace\tWorkspace\t0")
        with self.assertRaises(ToolError) as caught:
            asyncio.run(snapshot(studio))
        self.assertEqual(caught.exception.code, SIZE_LIMIT)

    def test_a_bad_path_never_reaches_the_studio(self):
        studio = _FakeStudio(_walk_output([]))
        with self.assertRaises(ToolError) as caught:
            asyncio.run(snapshot(studio, path="game.Workspace\nprint(1)"))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertEqual(studio.calls, [])

    def test_the_flags_say_what_was_left_out(self):
        self.assertIn("complete", describe_flags(False, False))
        self.assertIn("node cap", describe_flags(True, False))
        self.assertIn("depth cap", describe_flags(False, True))
        self.assertIn("both", describe_flags(True, True))

    def test_the_result_says_that_output_paths_may_not_be_input_paths(self):
        # Measured live: the tree contains a service called "Run Service", so
        # an output path can carry a name the identifier-only input path
        # cannot address. A consumer that reads one and feeds it back would
        # get INVALID_ARGUMENT with no explanation, so the result says so.
        studio = _FakeStudio(_walk_output(["game.Run Service\tRunService\t0"]))
        report = asyncio.run(snapshot(studio))
        self.assertEqual(report["nodes"][0]["path"], "game.Run Service")
        self.assertIn("space", report["note"])


class TestToolSurface(unittest.TestCase):
    """The tool is advertised, and what it costs is inside the cap."""

    def test_the_contract_carries_the_tool(self):
        with open(CONTRACT, encoding="utf-8") as handle:
            contract = json.load(handle)
        names = [tool["name"] for tool in contract["tools"]]
        self.assertIn("extended_tree_snapshot", names)
        self.assertEqual(len(names), len(set(names)))

    def test_the_description_is_inside_the_caps(self):
        with open(CONTRACT, encoding="utf-8") as handle:
            contract = json.load(handle)
        row = next(
            tool
            for tool in contract["tools"]
            if tool["name"] == "extended_tree_snapshot"
        )
        self.assertLessEqual(
            row["description_chars"], contract["per_tool_description_cap"]
        )
        self.assertLessEqual(
            contract["total_description_chars"],
            contract["total_description_cap"],
        )

    def test_the_tool_is_marked_read_only(self):
        # A snapshot only reads, so a client may run it alongside other
        # reads rather than serialising it against mutating calls.
        tool = next(
            tool
            for tool in es._EXTENDED_TOOLS
            if tool.name == "extended_tree_snapshot"
        )
        self.assertTrue(tool.read_only)

    def test_the_handler_is_registered(self):
        self.assertIn("extended_tree_snapshot", es._EXTENDED_HANDLERS)

    def test_an_unknown_argument_is_refused(self):
        # The server's highest-value check: a silently ignored parameter
        # produces a plausible wrong answer rather than an error.
        with self.assertRaises(ToolError) as caught:
            es._reject_unknown_arguments(
                "extended_tree_snapshot", {"depth": 2}
            )
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)

    def test_the_declared_arguments_are_accepted(self):
        es._reject_unknown_arguments(
            "extended_tree_snapshot",
            {"path": "game", "max_depth": 2, "max_nodes": 10, "studio_id": "x"},
        )


if __name__ == "__main__":
    unittest.main()
