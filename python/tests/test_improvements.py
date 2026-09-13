"""Regression tests for robustness fixes (no Studio required).

Covers writer long-bracket handling, updater ambiguity, console-watch diffing,
create_module wiring, search_and_read batching, run_tests polling, JSON parsing,
singleton ownership, and server disabled-tools filtering.
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from roblox_studio_mcp import CallToolResult, MCPClient, Tool
from roblox_studio_mcp.extended import writer as writer_mod
from roblox_studio_mcp.extended.writer import (
    _lua_long_bracket,
    _pick_bracket_level,
    _strip_line_prefixes,
    write_like_multi_edit,
)
from roblox_studio_mcp.extended.updater import update_like_multi_edit
from roblox_studio_mcp.extended import extensions as ext_mod
from roblox_studio_mcp.extended.extensions import (
    _ConsoleWatch,
    create_module_with_deps,
    get_watch_state,
    run_tests,
    script_search_and_read,
)
from roblox_studio_mcp.types import _extract_balanced
import roblox_studio_mcp.roblox as roblox_mod
import roblox_studio_mcp.server as server_mod


def _text_result(text):
    return CallToolResult.from_dict({"content": [{"type": "text", "text": text}]})


class FakeStudio:
    """Minimal RobloxStudio double recording calls."""

    def __init__(self, files=None, studio_id=None):
        self.files = dict(files or {})
        self.calls = []
        self.executed = []
        self._studio_id = studio_id
        self.started = 0
        self.stopped = 0
        self.console_lines = ["ok line"]
        self.fail_execute_with = None

    @property
    def studio_id(self):
        return self._studio_id

    async def script_read(self, target_file=None, **kwargs):
        path = target_file or kwargs.get("target_path") or kwargs.get("path")
        self.calls.append(("script_read", path))
        if path in self.files:
            src = self.files[path]
            numbered = "\n".join(f"{i+1}→{line}" for i, line in enumerate(src.splitlines()))
            return _text_result(numbered)
        raise RuntimeError(f"Script not found: {path}")

    async def execute_luau(self, code, datamodel_type="Edit", **kwargs):
        self.executed.append(code)
        if self.fail_execute_with:
            raise RuntimeError(self.fail_execute_with)
        return _text_result("ok")

    async def call(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        if name == "multi_edit":
            # Simulate atomic replace for writer/updater tests (and mutate files).
            target = (arguments or {}).get("file_path")
            edits = (arguments or {}).get("edits") or []
            if target in self.files:
                src = self.files[target]
                for e in edits:
                    src = src.replace(e["old_string"], e["new_string"])
                self.files[target] = src
            return _text_result("edited")
        if name == "get_console_output":
            return _text_result("\n".join(self.console_lines))
        if name == "list_roblox_studios":
            return _text_result(json.dumps({"studios": []}))
        return _text_result("{}")

    async def search_game_tree(self, **kwargs):
        self.calls.append(("search_game_tree", kwargs))
        return _text_result(json.dumps(self._tree_payload))

    async def start_play(self):
        self.started += 1
        return _text_result("started")

    async def stop_play(self):
        self.stopped += 1
        return _text_result("stopped")


class TestStripPrefixes(unittest.TestCase):
    def test_strips_numbered_prefix(self):
        self.assertEqual(_strip_line_prefixes("1→print(1)\n2→print(2)"), "print(1)\nprint(2)")

    def test_preserves_arrow_in_source(self):
        # Literal → without a leading line number must survive.
        self.assertEqual(_strip_line_prefixes("1→a → b"), "a → b")
        self.assertEqual(_strip_line_prefixes("x → y"), "x → y")

    def test_tolerates_whitespace(self):
        self.assertEqual(_strip_line_prefixes("  12  →hi"), "hi")


class TestLongBrackets(unittest.TestCase):
    def test_raw_no_escapes(self):
        payload = "a'b\"c\\d\nnewline → arrow"
        level = _pick_bracket_level(payload)
        lit = _lua_long_bracket(payload, level)
        self.assertIn(payload, lit)
        self.assertTrue(lit.startswith("[") and lit.endswith("]"))

    def test_picks_collision_free_level(self):
        colliding = "x ]==========] y"  # closes level-10
        level = _pick_bracket_level("name", colliding)
        self.assertNotEqual(level, 10)
        lit = _lua_long_bracket(colliding, level)
        self.assertIn(colliding, lit)
        # Outer bracket must use the new level, so the inner level-10 closer
        # can't terminate it early.
        self.assertTrue(lit.startswith(f"[{'=' * level}["))
        self.assertTrue(lit.endswith(f"]{'=' * level}]"))

    def test_chunked_builder_uses_raw_name(self):
        lua = writer_mod._build_chunked_lua("game.S", 'a"b', "Script", ["print(1)"], 10)
        self.assertIn('FindFirstChild([==========[a"b]==========])', lua)

    def test_long_bracket_guards_leading_newline(self):
        # Lua skips the first character of a long-bracket string when it is
        # a newline; the guard newline is consumed instead of the payload.
        lit = _lua_long_bracket("\nabc", 10)
        self.assertTrue(lit.startswith("[==========[\n"))
        self.assertIn("\nabc", lit)

    def test_long_bracket_no_guard_without_leading_newline(self):
        self.assertEqual(_lua_long_bracket("abc", 10), "[==========[abc]==========]")

    def test_chunked_builder_captures_loop_index(self):
        lua = writer_mod._build_chunked_lua("game.S", "A", "Script", ["x"], 10)
        self.assertIn("local idx = i", lua)
        self.assertIn("if idx == 1", lua)
        self.assertNotIn("if i == 1", lua)


class TestWriter(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_game_path(self):
        studio = FakeStudio()
        with self.assertRaises(ValueError):
            await write_like_multi_edit(studio, "/tmp/x.lua", "hi")

    async def test_rejects_bad_class(self):
        studio = FakeStudio(files={"game.S.A": "hi"})
        with self.assertRaises(ValueError):
            await write_like_multi_edit(studio, "game.S.A", "hi2", className="Part")

    async def test_unchanged_no_write(self):
        studio = FakeStudio(files={"game.S.A": "print(1)"})
        status = await write_like_multi_edit(studio, "game.S.A", "print(1)")
        self.assertEqual(status, "unchanged")
        self.assertFalse(any(c[0] == "multi_edit" for c in studio.calls))

    async def test_wrote_via_multi_edit(self):
        studio = FakeStudio(files={"game.S.A": "old"})
        status = await write_like_multi_edit(studio, "game.S.A", "new")
        self.assertEqual(status, "wrote")
        self.assertTrue(any(c[0] == "multi_edit" for c in studio.calls))

    async def test_created_when_missing(self):
        studio = FakeStudio(files={})
        status = await write_like_multi_edit(
            studio, "game.S.New", "print('hi')", create_if_missing=True
        )
        self.assertEqual(status, "created")
        self.assertTrue(studio.executed)
        # Long-bracket source, not single-quote escapes.
        self.assertIn("[==========[", studio.executed[0])

    async def test_missing_requires_flag(self):
        studio = FakeStudio(files={})
        with self.assertRaises(RuntimeError):
            await write_like_multi_edit(studio, "game.S.New", "x")

    async def test_connection_error_not_masked(self):
        studio = FakeStudio(files={})

        async def boom(*a, **k):
            raise RuntimeError("connection reset by peer")

        studio.script_read = boom
        with self.assertRaises(RuntimeError):
            await write_like_multi_edit(
                studio, "game.S.New", "x", create_if_missing=True
            )
        self.assertEqual(studio.executed, [])

    async def test_return_string_uses_long_bracket(self):
        studio = FakeStudio(files={"game.S.M": "old"})
        # Force the create path via missing file.
        studio2 = FakeStudio(files={})
        status = await write_like_multi_edit(
            studio2, "game.S.M", 'a"b\nc', create_if_missing=True, return_string=True
        )
        self.assertEqual(status, "created")
        code = studio2.executed[0]
        self.assertIn("return", code)
        self.assertIn("[==========[", code)
        self.assertNotIn('\\"', code)

    async def test_chunked_retry_caps(self):
        studio = FakeStudio(files={"game.S.Big": "old"})
        big = "x" * (writer_mod._STRING_PROPERTY_SIZE_LIMIT + 10)
        studio.fail_execute_with = "Lua parse error near slices"
        with self.assertRaises(RuntimeError):
            await writer_mod._chunked_write(studio, "game.S.Big", big)
        # 1 initial + 5 retries
        self.assertEqual(len(studio.executed), 6)


class TestUpdater(unittest.IsolatedAsyncioTestCase):
    async def test_skips_noop_and_missing(self):
        studio = FakeStudio(files={"game.S.A": "hello world"})
        result = await update_like_multi_edit(
            studio,
            "game.S.A",
            edits=[("hello", "hi"), ("same", "same"), ("nope", "x")],
        )
        self.assertEqual(result.updated, [0])
        self.assertEqual(result.skipped_no_op, [1])
        self.assertEqual(result.skipped_no_match, [2])
        self.assertTrue(result.warnings)

    async def test_skips_ambiguous_duplicates(self):
        studio = FakeStudio(files={"game.S.A": "foo foo foo"})
        result = await update_like_multi_edit(studio, "game.S.A", [("foo", "bar")])
        self.assertEqual(result.skipped_ambiguous, [0])
        self.assertEqual(result.updated, [])
        # Nothing sent downstream when all edits skipped.
        self.assertFalse(any(c[0] == "multi_edit" for c in studio.calls))

    async def test_strict_raises_on_ambiguous(self):
        studio = FakeStudio(files={"game.S.A": "foo foo"})
        with self.assertRaises(ValueError):
            await update_like_multi_edit(
                studio, "game.S.A", [("foo", "bar")], skip_missing=False
            )

    async def test_strict_raises_on_noop(self):
        studio = FakeStudio(files={"game.S.A": "hi"})
        with self.assertRaises(ValueError):
            await update_like_multi_edit(
                studio, "game.S.A", [("hi", "hi")], skip_no_ops=False
            )

    async def test_replace_all_replaces_every_occurrence(self):
        studio = FakeStudio(files={"game.S.A": "foo foo foo"})
        result = await update_like_multi_edit(
            studio,
            "game.S.A",
            [{"old_string": "foo", "new_string": "bar", "replace_all": True}],
        )
        self.assertEqual(result.updated, [0])
        self.assertEqual(result.skipped_ambiguous, [])
        self.assertEqual(studio.files["game.S.A"], "bar bar bar")

    async def test_replace_all_camel_case_alias(self):
        studio = FakeStudio(files={"game.S.A": "a,a,a"})
        result = await update_like_multi_edit(
            studio,
            "game.S.A",
            [{"oldString": "a", "newString": "b", "replaceAll": True}],
        )
        self.assertEqual(result.updated, [0])
        self.assertEqual(studio.files["game.S.A"], "b,b,b")

    async def test_sequential_edits_operate_on_previous_results(self):
        studio = FakeStudio(files={"game.S.A": "hello world"})
        result = await update_like_multi_edit(
            studio,
            "game.S.A",
            [
                {"old_string": "hello", "new_string": "hi", "replace_all": True},
                {"old_string": "hi world", "new_string": "hi there"},
            ],
        )
        self.assertEqual(result.updated, [0, 1])
        self.assertEqual(studio.files["game.S.A"], "hi there")

    async def test_strict_errors_read_like_edit_errors(self):
        studio = FakeStudio(files={"game.S.A": "hello"})
        with self.assertRaisesRegex(ValueError, "not found in content"):
            await update_like_multi_edit(
                studio, "game.S.A", [("nope", "x")], skip_missing=False
            )
        studio = FakeStudio(files={"game.S.A": "foo foo"})
        with self.assertRaisesRegex(ValueError, "multiple matches"):
            await update_like_multi_edit(
                studio, "game.S.A", [("foo", "bar")], skip_missing=False
            )


class TestConsoleWatch(unittest.IsolatedAsyncioTestCase):
    async def test_first_call_returns_tail(self):
        studio = FakeStudio()
        studio.console_lines = [f"line {i}" for i in range(50)]
        watch = _ConsoleWatch()
        out = await watch.poll(studio)
        self.assertEqual(len(out["new_lines"]), 20)
        self.assertEqual(out["total_lines"], 50)

    async def test_append_only_returns_delta(self):
        studio = FakeStudio()
        watch = _ConsoleWatch()
        studio.console_lines = ["a", "b"]
        await watch.poll(studio)
        studio.console_lines = ["a", "b", "c"]
        out = await watch.poll(studio)
        self.assertEqual(out["new_lines"], ["c"])

    async def test_duplicate_last_line_uses_last_occurrence(self):
        watch = _ConsoleWatch()
        watch._last_lines = ["x", "dup", "y"]
        studio = FakeStudio()
        studio.console_lines = ["x", "dup", "y", "dup", "z"]
        # Simulate poll without resetting: set last then diff.
        out = await watch.poll(studio)
        # Fast path fails (prefix differs), fallback finds last "y" at index 2.
        self.assertEqual(out["new_lines"], ["dup", "z"])

    async def test_truncation_returns_all(self):
        watch = _ConsoleWatch()
        watch._last_lines = ["old1", "old2"]
        studio = FakeStudio()
        studio.console_lines = ["new1"]
        out = await watch.poll(studio)
        self.assertEqual(out["new_lines"], ["new1"])

    def test_get_watch_state_persistent(self):
        ext_mod._WATCH_STATES.clear()
        self.assertIs(get_watch_state("s1"), get_watch_state("s1"))
        self.assertIsNot(get_watch_state("s1"), get_watch_state("s2"))


class TestCreateModule(unittest.IsolatedAsyncioTestCase):
    async def test_appends_require_once(self):
        studio = FakeStudio(
            files={"game.ReplicatedStorage.Util": "return {}", "game.ServerScriptService.Main": "print(1)\n"}
        )
        # Point writer at the fake studio files.
        orig = writer_mod.write_like_multi_edit

        async def fake_write(st, path, content, **kw):
            st.files[path] = content
            return "wrote"

        with mock.patch.object(writer_mod, "write_like_multi_edit", side_effect=fake_write):
            # create_module imports writer inside the function, so patch the module attr.
            import roblox_studio_mcp.extended.writer as wmod
            with mock.patch.object(wmod, "write_like_multi_edit", side_effect=fake_write):
                # Need the fake studio to serve script_read from files.
                status = await create_module_with_deps(
                    studio,
                    "game.ReplicatedStorage.NewMod",
                    "return 42",
                    require_target_path="game.ServerScriptService.Main",
                    require_statement='require(game.ReplicatedStorage.NewMod)',
                )
                self.assertIn(status, ("wrote", "created", "unchanged"))

    async def test_skips_when_require_present(self):
        req = "require(game.ReplicatedStorage.Util)"
        studio = FakeStudio(files={"game.S.Main": f"print(1)\n{req}\n"})
        with mock.patch(
            "roblox_studio_mcp.extended.writer.write_like_multi_edit",
            wraps=writer_mod.write_like_multi_edit,
        ) as spy:
            # First call creates the module; second (target rewrite) must not happen.
            studio.files["game.S.Mod"] = "old"
            await create_module_with_deps(
                studio,
                "game.S.Mod",
                "old",
                require_target_path="game.S.Main",
                require_statement=req,
            )
            # Only the module write path touched multi_edit/execute; target unchanged.
            self.assertIn(req, studio.files["game.S.Main"])

    async def test_require_append_is_idempotent(self):
        studio = FakeStudio(
            files={
                "game.ReplicatedStorage.NewMod": "old",
                "game.ServerScriptService.Main": "print(1)\n",
            }
        )
        req = "require(game.ReplicatedStorage.NewMod)"
        for _ in range(2):
            await create_module_with_deps(
                studio,
                "game.ReplicatedStorage.NewMod",
                "return 42",
                require_target_path="game.ServerScriptService.Main",
                require_statement=req,
            )
        self.assertEqual(
            studio.files["game.ServerScriptService.Main"].count(req), 1
        )


class TestSearchAndRead(unittest.IsolatedAsyncioTestCase):
    async def test_batches_and_filters(self):
        studio = FakeStudio()
        studio._tree_payload = [
            {"className": "Script", "fullPath": "game.S.A", "name": "A"},
            {"className": "Part", "fullPath": "game.S.P", "name": "P"},
            {"className": "ModuleScript", "fullPath": "game.S.M", "name": "M"},
        ]
        studio.files = {"game.S.A": "a-src", "game.S.M": "m-src"}

        async def fake_read(target_file=None, **k):
            path = target_file
            src = studio.files[path]
            return _text_result(f"1→{src}")

        studio.script_read = fake_read
        out = await script_search_and_read(studio, "game.S", max_results=10)
        self.assertEqual([r["path"] for r in out], ["game.S.A", "game.S.M"])

    async def test_returns_empty_on_bad_json(self):
        studio = FakeStudio()
        studio._tree_payload = None

        async def bad_tree(**k):
            return _text_result("not json at all {{{")

        studio.search_game_tree = bad_tree
        out = await script_search_and_read(studio, "game.S")
        self.assertEqual(out, [])

    async def test_truncates_long_sources_by_default(self):
        studio = FakeStudio()
        studio._tree_payload = [
            {"className": "Script", "fullPath": "game.S.Big", "name": "Big"},
        ]
        lines = [f"print({i}) -- padding to grow the source" for i in range(100)]
        studio.files = {"game.S.Big": "\n".join(lines)}
        out = await script_search_and_read(studio, "game.S")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["truncated"])
        self.assertEqual(len(out[0]["source"]), 2000)
        self.assertEqual(out[0]["line_count"], 100)

    async def test_returns_full_source_when_truncation_disabled(self):
        studio = FakeStudio()
        studio._tree_payload = [
            {"className": "Script", "fullPath": "game.S.Big", "name": "Big"},
        ]
        lines = [f"print({i}) -- padding to grow the source" for i in range(100)]
        full = "\n".join(lines)
        studio.files = {"game.S.Big": full}
        out = await script_search_and_read(
            studio, "game.S", max_chars_per_source=0
        )
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["truncated"])
        self.assertEqual(out[0]["source"], full)
        self.assertEqual(out[0]["line_count"], 100)


class TestRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_passes_without_errors(self):
        studio = FakeStudio()
        studio.console_lines = ["info: ok", "0 errors"]
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            out = await run_tests(studio, wait_seconds=0.1)
        self.assertTrue(out["passed"])
        self.assertEqual(studio.started, 1)
        self.assertEqual(studio.stopped, 1)

    async def test_detects_errors_case_insensitive(self):
        studio = FakeStudio()
        studio.console_lines = ["SCRIPT ERROR: boom", "done"]
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            out = await run_tests(studio, wait_seconds=0.1)
        self.assertFalse(out["passed"])
        self.assertTrue(out["errors"])

    async def test_missing_test_paths_fail(self):
        studio = FakeStudio(files={})
        studio.console_lines = ["all good"]
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            out = await run_tests(studio, test_paths=["game.S.Missing"], wait_seconds=0.1)
        self.assertFalse(out["passed"])
        self.assertTrue(any("Missing" in e for e in out["errors"]))

    async def test_always_stops_play(self):
        studio = FakeStudio()
        studio.console_lines = ["x"]

        async def boom(*a, **k):
            raise RuntimeError("console exploded")

        studio.call = boom
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            with self.assertRaises(RuntimeError):
                await run_tests(studio, wait_seconds=0.1)
        self.assertEqual(studio.stopped, 1)


class TestExtendedToolDescriptions(unittest.TestCase):
    def test_recommends_extended_tools_over_raw_multi_edit(self):
        from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS

        by_name = {t.name: t.description for t in _EXTENDED_TOOLS}
        self.assertTrue(
            by_name["extended_write_like_multi_edit"].startswith(
                "RECOMMENDED over raw multi_edit"
            )
        )
        self.assertTrue(
            by_name["extended_update_like_multi_edit"].startswith(
                "RECOMMENDED over raw multi_edit"
            )
        )
        self.assertIn("replace_all", by_name["extended_update_like_multi_edit"])
        schema = next(
            t.input_schema
            for t in _EXTENDED_TOOLS
            if t.name == "extended_update_like_multi_edit"
        )
        item_props = schema["properties"]["edits"]["items"]["properties"]
        self.assertIn("replace_all", item_props)

    def test_search_tool_documents_truncation(self):
        from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS

        tool = next(
            t
            for t in _EXTENDED_TOOLS
            if t.name == "extended_script_search_and_read"
        )
        self.assertIn("truncated", tool.description)
        self.assertIn(
            "max_chars_per_source", tool.input_schema["properties"]
        )


class TestJsonParsing(unittest.TestCase):
    def _res(self, text):
        return CallToolResult.from_dict({"content": [{"type": "text", "text": text}]})

    def test_fenced_with_lang(self):
        self.assertEqual(self._res('```json\n{"a": 1}\n```').json(), {"a": 1})

    def test_fenced_without_lang(self):
        self.assertEqual(self._res('```\n[1,2]\n```').json(), [1, 2])

    def test_embedded_prefers_first_balanced(self):
        self.assertEqual(self._res('Here: {"a": {"b": 1}} tail {"c": 2}').json(), {"a": {"b": 1}})

    def test_braces_in_strings(self):
        self.assertEqual(self._res('x {"a": "} {"} y').json(), {"a": "} {"})

    def test_none_when_no_json(self):
        self.assertIsNone(self._res("just prose").json())

    def test_extract_balanced(self):
        self.assertEqual(_extract_balanced('{"a":1} tail'), '{"a":1}')
        self.assertIsNone(_extract_balanced("nope"))

    def test_is_error_snake_case_alias(self):
        res = CallToolResult.from_dict(
            {"content": [{"type": "text", "text": "x"}], "is_error": True}
        )
        self.assertTrue(res.is_error)
        res = CallToolResult.from_dict(
            {"content": [{"type": "text", "text": "x"}], "isError": True}
        )
        self.assertTrue(res.is_error)


class TestSingletonOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        roblox_mod._singleton = None

    async def test_aexit_keeps_singleton(self):
        fake_client = mock.AsyncMock()
        fake_client.is_connected = True
        studio = roblox_mod.RobloxStudio(fake_client, _is_singleton=True)
        await studio.__aexit__(None, None, None)
        fake_client.close.assert_not_awaited()

    async def test_aexit_closes_owned(self):
        fake_client = mock.AsyncMock()
        studio = roblox_mod.RobloxStudio(fake_client, _is_singleton=False)
        await studio.__aexit__(None, None, None)
        fake_client.close.assert_awaited_once()

    async def test_set_studio_id_and_refresh(self):
        client = FakeStudio()
        studio = roblox_mod.RobloxStudio(client, studio_id="a")

        async def fake_list():
            return [Tool("t1"), Tool("t2")]

        client.list_tools = fake_list
        studio.set_studio_id("b")
        self.assertEqual(studio.studio_id, "b")
        first = await studio.list_tools()
        self.assertEqual(len(first), 2)

        async def fake_list2():
            return [Tool("t3")]

        client.list_tools = fake_list2
        cached = await studio.list_tools()
        self.assertEqual(len(cached), 2)  # cached
        refreshed = await studio.list_tools(refresh=True)
        self.assertEqual([t.name for t in refreshed], ["t3"])


class TestServerDisabledTools(unittest.IsolatedAsyncioTestCase):
    async def test_list_filters_disabled(self):
        class FakeClient:
            disabled_tools = {"secret"}

            async def request(self, method, params):
                return {"tools": [{"name": "ok"}, {"name": "secret"}]}

        sent = []
        with mock.patch.object(server_mod, "_send", side_effect=lambda m: sent.append(m)):
            await server_mod._handle_message(
                FakeClient(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            )
        names = [t["name"] for t in sent[0]["result"]["tools"]]
        self.assertEqual(names, ["ok"])

    async def test_call_rejects_disabled(self):
        class FakeClient:
            disabled_tools = {"secret"}

            async def request(self, method, params):
                raise AssertionError("should not forward")

        sent = []
        with mock.patch.object(server_mod, "_send", side_effect=lambda m: sent.append(m)):
            await server_mod._handle_message(
                FakeClient(),
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "secret", "arguments": {}}},
            )
        self.assertIn("error", sent[0])
        self.assertEqual(sent[0]["error"]["code"], -32602)


class TestInsertValidation(unittest.IsolatedAsyncioTestCase):
    async def test_missing_file_raises(self):
        studio = FakeStudio()
        with self.assertRaises(FileNotFoundError):
            await ext_mod.insert_asset_from_file(studio, "/nonexistent/xyz.lua")

    async def test_bad_parent_raises(self):
        studio = FakeStudio()
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
            f.write("print(1)")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                await ext_mod.insert_asset_from_file(studio, path, parent_path="/tmp")
        finally:
            os.unlink(path)


class TestExecuteFromFile(unittest.IsolatedAsyncioTestCase):
    async def test_empty_file_raises(self):
        studio = FakeStudio()
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
            f.write("   \n")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                await ext_mod.execute_luau_from_file(studio, path)
        finally:
            os.unlink(path)

    async def test_normalizes_crlf(self):
        studio = FakeStudio()
        with tempfile.NamedTemporaryFile(
            "w", suffix=".lua", delete=False, newline=""
        ) as f:
            f.write("return 1\r\n")
            path = f.name
        try:
            captured = {}

            async def fake_execute(code, datamodel_type="Edit", **kwargs):
                captured["code"] = code
                return _text_result("ok")

            studio.execute_luau = fake_execute
            await ext_mod.execute_luau_from_file(studio, path)
            self.assertEqual(captured["code"], "return 1\n")
        finally:
            os.unlink(path)


class TestInsertCrlf(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_crlf_on_script_insert(self):
        studio = FakeStudio()
        with tempfile.NamedTemporaryFile(
            "w", suffix=".lua", delete=False, newline=""
        ) as f:
            f.write("print(1)\r\nprint(2)\r\n")
            path = f.name
        try:
            captured = {}

            async def fake_write(st, target_path, content, **kwargs):
                captured["content"] = content
                return "created"

            import roblox_studio_mcp.extended.writer as wmod

            with mock.patch.object(
                wmod, "write_like_multi_edit", side_effect=fake_write
            ):
                await ext_mod.insert_asset_from_file(
                    studio, path, parent_path="game.Workspace"
                )
            self.assertNotIn("\r", captured["content"])
            self.assertIn("print(1)\nprint(2)", captured["content"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
