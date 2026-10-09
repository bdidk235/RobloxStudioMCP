"""A3 (2026-10-09): one shared game-tree path validator.

``target_path`` used to get only a ``startswith("game.")`` check while the
container half was spliced RAW into generated Luau
(``local parent = {container}``) — so ``game.Workspace\\nprint("PWNED")\\n--.Evil``
executed ``print("PWNED")`` in the Editor (PoC-measured before the fix).
Every caller-named DataModel path now goes through
``writer.validate_game_tree_path`` — ``target_path`` (write/update),
``parent_path`` (insert) and ``root_path`` (search_and_read, grep) — and
anything with whitespace or a newline is refused with INVALID_ARGUMENT
carrying the received value.
"""

import asyncio
import unittest

from roblox_studio_mcp.extended.errors import INVALID_ARGUMENT, ToolError
from roblox_studio_mcp.extended.extensions import (
    insert_asset_from_file,
    script_search_and_read,
)
from roblox_studio_mcp.extended.grep import extended_script_grep
from roblox_studio_mcp.extended.updater import update_script
from roblox_studio_mcp.extended.writer import (
    _split_target,
    validate_game_tree_path,
    write_script,
)

EVIL = 'game.Workspace\nprint("PWNED")\n--.Evil'

BAD_PATHS = (
    EVIL,  # the PoC: newline lands executed code in the Editor
    "game.Workspace.Evil Script",  # whitespace
    "game.Workspace.Evil\tScript",  # tab
    "game.Workspace.Evil;print(1)",  # statement separator
    'game.Workspace.Evil")print(1)--',  # quote break-out
    "game.Workspace.Evil-print(1)",  # parens are not identifiers
    "game.Workspace.",  # trailing dot: empty segment
    "game..Workspace",  # empty segment
    "game",  # bare root names no instance
    "Workspace.Foo",  # no game. root
    "Game.Workspace.Foo",  # wrong case on the root
    "",  # empty
    "game.1abc",  # leading digit is not a Luau identifier
    "game.Workspace.Foo Bar.Baz",
)

GOOD_PATHS = (
    "game.Workspace",
    "game.S",
    "game.ServerScriptService.MyScript",
    "game.ReplicatedStorage._private.v2",
)


class FakeStudio:
    """Never reaches the transport: validation fires before any Studio call."""

    def __init__(self):
        self.calls = []

    async def script_read(self, target_path, **kwargs):
        self.calls.append(("script_read", target_path))
        raise AssertionError("validation must fire before script_read")

    async def execute_luau(self, code, **kwargs):
        self.calls.append(("execute_luau", code))
        raise AssertionError("validation must fire before execute_luau")

    async def call(self, *args, **kwargs):
        self.calls.append(("call", args))
        raise AssertionError("validation must fire before call")

    async def search_game_tree(self, **kwargs):
        self.calls.append(("search_game_tree", kwargs))
        raise AssertionError("validation must fire before search_game_tree")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class SharedValidator(unittest.TestCase):
    def test_accepts_well_formed_paths(self):
        for good in GOOD_PATHS:
            self.assertEqual(
                validate_game_tree_path(good, field="target_path"), good
            )

    def test_rejects_everything_else_with_value_in_error(self):
        from roblox_studio_mcp.extended.errors import describe

        for bad in BAD_PATHS:
            with self.assertRaises(ToolError, msg=repr(bad)) as caught:
                validate_game_tree_path(bad, field="target_path")
            self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
            # The received value travels in the error, JSON-rendered.
            self.assertIn(describe(bad), caught.exception.message)

    def test_rejects_non_strings(self):
        for bad in (None, 42, b"game.S", ["game.S"]):
            with self.assertRaises(ToolError, msg=repr(bad)) as caught:
                validate_game_tree_path(bad, field="root_path")  # type: ignore[arg-type]
            self.assertEqual(caught.exception.code, INVALID_ARGUMENT)


class EveryEntryPointRefusesThePoC(unittest.TestCase):
    """The newline PoC must be refused at every path-taking entry point,
    before any Studio call."""

    def test_split_target(self):
        with self.assertRaises(ToolError) as caught:
            _split_target(EVIL)
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)

    def test_write_script(self):
        studio = FakeStudio()
        with self.assertRaises(ToolError) as caught:
            _run(write_script(studio, EVIL, "print(1)", create_if_missing=True))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertEqual(studio.calls, [])

    def test_update_script(self):
        studio = FakeStudio()
        with self.assertRaises(ToolError) as caught:
            _run(update_script(studio, EVIL, [("a", "b")]))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertEqual(studio.calls, [])

    def test_search_and_read_root_path(self):
        studio = FakeStudio()
        with self.assertRaises(ToolError) as caught:
            _run(script_search_and_read(studio, EVIL))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertEqual(studio.calls, [])

    def test_insert_parent_path_refused_before_studio(self):
        import os

        studio = FakeStudio()
        # __file__ exists inside the working tree, so confinement and the
        # existence check pass and the failure is the parent_path itself.
        with self.assertRaises(ToolError) as caught:
            _run(
                insert_asset_from_file(
                    studio, os.path.abspath(__file__), parent_path=EVIL
                )
            )
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertIn("parent_path", caught.exception.message)
        self.assertEqual(studio.calls, [])

    def test_grep_root_path(self):
        studio = FakeStudio()
        with self.assertRaises(ToolError) as caught:
            _run(extended_script_grep(studio, "print", root_path=EVIL))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        self.assertEqual(studio.calls, [])


class EveryEntryPointRefusesWhitespace(unittest.TestCase):
    def test_all_string_paths(self):
        spaced = "game.Workspace.Evil Script"
        with self.assertRaises(ToolError) as caught:
            _split_target(spaced)
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        studio = FakeStudio()
        with self.assertRaises(ToolError) as caught:
            _run(write_script(studio, spaced, "print(1)"))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        with self.assertRaises(ToolError) as caught:
            _run(update_script(studio, spaced, [("a", "b")]))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        with self.assertRaises(ToolError) as caught:
            _run(script_search_and_read(studio, spaced))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)
        with self.assertRaises(ToolError) as caught:
            _run(extended_script_grep(studio, "print", root_path=spaced))
        self.assertEqual(caught.exception.code, INVALID_ARGUMENT)


if __name__ == "__main__":
    unittest.main()
