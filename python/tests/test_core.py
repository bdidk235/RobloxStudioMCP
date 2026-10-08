"""Tests for the pure-logic parts of roblox_studio_mcp (no Studio required).

Run with either:
    python -m unittest discover -s tests
    pytest
"""

import unittest
from unittest import mock

import roblox_studio_mcp.roblox as roblox_mod
import roblox_studio_mcp.server as server_mod
from roblox_studio_mcp import (
    CallToolResult,
    MCPClient,
    MCPConnectionError,
    MCPToolError,
    RobloxStudio,
    Tool,
    close_singleton,
    get_singleton,
)


class TestTool(unittest.TestCase):
    def test_from_dict(self):
        tool = Tool.from_dict(
            {
                "name": "execute_luau",
                "description": "run luau",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "datamodel_type": {"type": "string"},
                        "studio_id": {"type": "string"},
                    },
                    "required": ["code", "datamodel_type", "studio_id"],
                },
            }
        )
        self.assertEqual(tool.name, "execute_luau")
        self.assertEqual(tool.description, "run luau")
        self.assertTrue(tool.has_parameter("studio_id"))
        self.assertFalse(tool.has_parameter("nope"))
        self.assertEqual(tool.required, ["code", "datamodel_type", "studio_id"])


class TestCallToolResult(unittest.TestCase):
    def test_text_concatenation(self):
        result = CallToolResult.from_dict(
            {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "world"},
                ]
            }
        )
        self.assertEqual(result.text(), "hello\nworld")

    def test_json_plain(self):
        result = CallToolResult.from_dict(
            {"content": [{"type": "text", "text": '{"studios": [{"id": "a", "name": "Place1"}]}'}]}
        )
        self.assertEqual(result.json(), {"studios": [{"id": "a", "name": "Place1"}]})

    def test_json_fenced(self):
        result = CallToolResult.from_dict(
            {"content": [{"type": "text", "text": "```json\n{\"a\": 1}\n```"}]}
        )
        self.assertEqual(result.json(), {"a": 1})

    def test_json_embedded_in_prose(self):
        result = CallToolResult.from_dict(
            {"content": [{"type": "text", "text": "Here it is: [1, 2, 3] ok"}]}
        )
        self.assertEqual(result.json(), [1, 2, 3])


class FakeClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [
            Tool("list_roblox_studios"),  # no studio_id in schema
            Tool(
                "execute_luau",
                input_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}, "studio_id": {"type": "string"}},
                    "required": ["code", "studio_id"],
                },
            ),
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return CallToolResult.from_dict({"content": [{"type": "text", "text": "{}"}]})


class TestRobloxStudio(unittest.IsolatedAsyncioTestCase):
    async def test_studio_id_injected_when_declared(self):
        client = FakeClient()
        studio = RobloxStudio(client, studio_id="sid-1")
        await studio.execute_luau("return 1")
        name, args = client.calls[-1]
        self.assertEqual(name, "execute_luau")
        self.assertEqual(args["studio_id"], "sid-1")
        self.assertEqual(args["code"], "return 1")

    async def test_studio_id_not_injected_when_absent(self):
        client = FakeClient()
        studio = RobloxStudio(client, studio_id="sid-1")
        await studio.call("list_roblox_studios", {})
        name, args = client.calls[-1]
        self.assertEqual(name, "list_roblox_studios")
        self.assertNotIn("studio_id", args)

    async def test_explicit_studio_id_wins(self):
        client = FakeClient()
        studio = RobloxStudio(client, studio_id="sid-1")
        await studio.call("execute_luau", {"studio_id": "override", "code": "x"})
        name, args = client.calls[-1]
        self.assertEqual(args["studio_id"], "override")


NOT_READY_TEXT = (
    "Unable to reach Roblox Studio right now. Ask the user to confirm that "
    "Studio is running with the MCP server enabled in Assistant settings."
)


class FlakyListClient:
    """Fails list_roblox_studios a set number of times, then succeeds."""

    def __init__(self, failures=float("inf"), mode="not-ready"):
        self.failures = failures
        self.mode = mode
        self.attempts = 0

    async def list_tools(self):
        return [
            Tool(
                "execute_luau",
                input_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}, "studio_id": {"type": "string"}},
                    "required": ["code", "studio_id"],
                },
            ),
        ]

    async def close(self):
        pass

    async def call_tool(self, name, arguments):
        if name == "list_roblox_studios":
            self.attempts += 1
            if self.mode == "boom":
                raise RuntimeError("boom")
            if self.attempts <= self.failures:
                raise MCPToolError(f"Tool {name!r} reported an error: {NOT_READY_TEXT}")
            if self.mode == "empty":
                text = '{"studios": []}'
            elif self.mode == "multi":
                text = (
                    '{"studios": [{"id": "sid-a", "name": "Place1"},'
                    ' {"id": "sid-b", "name": "rbx-re"}]}'
                )
            elif self.mode == "weird-keys":
                text = '{"data": {"a": 1}}'
            elif self.mode == "not-a-list":
                text = '{"studios": {"a": 1}}'
            elif self.mode == "scalar":
                text = '"nope"'
            else:
                text = '{"studios": [{"id": "sid-9", "name": "P"}]}'
            return CallToolResult.from_dict({"content": [{"type": "text", "text": text}]})
        return CallToolResult.from_dict({"content": [{"type": "text", "text": "ok"}]})


class StaleToolClient(FlakyListClient):
    """Lists fine, but reports every other tool as targeting a dead Studio."""

    async def call_tool(self, name, arguments):
        if name != "list_roblox_studios":
            raise MCPToolError(
                "The requested `studio_id` is not connected - that Roblox "
                "Studio instance may have been closed or its place unloaded."
            )
        return await super().call_tool(name, arguments)


class TestResolveReadiness(unittest.IsolatedAsyncioTestCase):
    async def test_rides_through_transient_not_ready(self):
        client = FlakyListClient(failures=2)
        studio = RobloxStudio(client)
        sid = await studio.resolve_studio_id(timeout=5.0, interval=0.01)
        self.assertEqual(sid, "sid-9")
        self.assertEqual(client.attempts, 3)

    async def test_gives_up_after_timeout(self):
        client = FlakyListClient(failures=float("inf"))
        studio = RobloxStudio(client)
        with self.assertRaises(MCPToolError):
            await studio.resolve_studio_id(timeout=0.05, interval=0.01)
        self.assertGreater(client.attempts, 1)
        self.assertLess(client.attempts, 50)

    async def test_other_errors_raise_immediately(self):
        client = FlakyListClient(mode="boom")
        studio = RobloxStudio(client)
        with self.assertRaises(RuntimeError):
            await studio.resolve_studio_id(timeout=5.0, interval=0.01)
        self.assertEqual(client.attempts, 1)

    async def test_empty_list_raises_immediately(self):
        client = FlakyListClient(failures=0, mode="empty")
        studio = RobloxStudio(client)
        with self.assertRaises(MCPToolError):
            await studio.resolve_studio_id(timeout=5.0, interval=0.01)
        self.assertEqual(client.attempts, 1)

    async def test_multiple_studios_raise_rather_than_guess(self):
        """An implicit id is only accepted when exactly one Studio is open."""
        client = FlakyListClient(failures=0, mode="multi")
        studio = RobloxStudio(client)
        with self.assertRaises(MCPToolError) as ctx:
            await studio.resolve_studio_id(timeout=5.0, interval=0.01)
        message = str(ctx.exception)
        self.assertIn("2 Roblox Studio instances", message)
        # the error must name the candidates so a caller can choose
        self.assertIn("sid-a", message)
        self.assertIn("sid-b", message)
        self.assertEqual(studio.studio_id, None)

    async def test_multiple_studios_rechecked_on_every_call(self):
        """No caching: a second Studio opening later must be noticed."""
        client = FlakyListClient(failures=0, mode="multi")
        studio = RobloxStudio(client)
        for _ in range(3):
            with self.assertRaises(MCPToolError):
                await studio.resolve_studio_id(timeout=5.0, interval=0.01)
        self.assertEqual(client.attempts, 3)

    async def test_single_studio_is_accepted_and_not_cached(self):
        client = FlakyListClient(failures=0)
        studio = RobloxStudio(client)
        self.assertEqual(await studio.resolve_studio_id(timeout=5.0), "sid-9")
        # resolved every call, so a second Studio is caught next time
        self.assertEqual(await studio.resolve_studio_id(timeout=5.0), "sid-9")
        self.assertEqual(client.attempts, 2)
        self.assertIsNone(studio.studio_id)

    async def test_explicit_id_is_returned_without_listing(self):
        client = FlakyListClient(failures=0, mode="multi")
        studio = RobloxStudio(client, "sid-explicit")
        self.assertEqual(await studio.resolve_studio_id(timeout=5.0), "sid-explicit")
        self.assertEqual(client.attempts, 0)

    async def test_unrecognised_payload_shape_raises_not_empty(self):
        """Schema drift must not masquerade as "no Studio connected"."""
        for mode in ("weird-keys", "not-a-list", "scalar"):
            with self.subTest(mode=mode):
                client = FlakyListClient(failures=0, mode=mode)
                studio = RobloxStudio(client)
                with self.assertRaises(MCPToolError) as ctx:
                    await studio.list_studios()
                message = str(ctx.exception)
                # it must name the shape problem, and must not claim
                # Studio is disconnected (which sends the caller to the
                # MCP toggle instead of at the real fault)
                self.assertIn("list_roblox_studios returned", message)
                self.assertNotIn("No Roblox Studio instances", message)

    async def test_genuinely_empty_list_is_still_empty(self):
        client = FlakyListClient(failures=0, mode="empty")
        studio = RobloxStudio(client)
        self.assertEqual(await studio.list_studios(), [])

    async def test_stale_pinned_id_explains_itself(self):
        client = StaleToolClient(failures=0)
        studio = RobloxStudio(client, "sid-dead")
        with self.assertRaises(MCPToolError) as ctx:
            await studio.call("get_studio_state", {})
        message = str(ctx.exception)
        self.assertIn("pinned studio_id", message)
        self.assertIn("sid-dead", message)
        self.assertIn("set_studio_id(None)", message)
        # the pin is not silently swapped
        self.assertEqual(studio.studio_id, "sid-dead")

    async def test_stale_id_on_implicit_path_is_not_rewritten(self):
        """An implicit failure passes through unchanged; inference re-lists next call."""
        client = StaleToolClient(failures=0)
        studio = RobloxStudio(client)
        with self.assertRaises(MCPToolError) as ctx:
            await studio.call("execute_luau", {"code": "return 1"})
        message = str(ctx.exception)
        self.assertNotIn("pinned studio_id", message)
        self.assertIn("is not connected", message)

    async def test_singleton_first_use_rides_through(self):
        roblox_mod._singleton = None
        client = FlakyListClient(failures=2)
        studio = RobloxStudio(client, _is_singleton=True)
        with mock.patch.object(
            roblox_mod.RobloxStudio, "connect", new=mock.AsyncMock(return_value=studio)
        ):
            first = await get_singleton()
            result = await first.execute_luau("return 1 + 1")
        self.assertEqual(result.text(), "ok")
        # an implicit resolution is never stored, so it cannot go stale
        self.assertIsNone(studio.studio_id)
        self.assertEqual(client.attempts, 3)
        await close_singleton()
        self.assertIsNone(roblox_mod._singleton)


class TestDisabledTools(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_tools_property(self):
        client = MCPClient("cmd.exe", ["/c", "echo"], disabled_tools={"foo", "bar"})
        self.assertEqual(client.disabled_tools, {"foo", "bar"})

    async def test_call_tool_raises_for_disabled(self):
        client = MCPClient("cmd.exe", ["/c", "echo"], disabled_tools={"foo"})
        with self.assertRaises(MCPToolError):
            await client.call_tool("foo", {})

    async def test_call_tool_proceeds_for_enabled(self):
        client = MCPClient("cmd.exe", ["/c", "echo"], disabled_tools={"foo"})
        # "bar" is not disabled, so it should get past the disabled check and
        # fail only because we are not connected (MCPConnectionError, not MCPToolError).
        with self.assertRaises(MCPConnectionError):
            await client.call_tool("bar", {})


class _FakeStudio:
    def __init__(self):
        self.closed = False

        class _C:
            is_connected = True

        self.client = _C()

    async def close(self):
        self.closed = True


class TestSingleton(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        roblox_mod._singleton = None

    async def test_returns_same_instance(self):
        fake = _FakeStudio()
        with mock.patch.object(
            roblox_mod.RobloxStudio, "connect", new=mock.AsyncMock(return_value=fake)
        ):
            a = await get_singleton()
            b = await get_singleton()
        self.assertIs(a, b)
        self.assertIs(a, fake)

    async def test_close_resets(self):
        fake = _FakeStudio()
        with mock.patch.object(
            roblox_mod.RobloxStudio, "connect", new=mock.AsyncMock(return_value=fake)
        ):
            await get_singleton()
            await close_singleton()
        self.assertTrue(fake.closed)
        self.assertIsNone(roblox_mod._singleton)

    async def test_connect_defaults_to_singleton(self):
        fake = _FakeStudio()
        with mock.patch.object(
            roblox_mod, "get_singleton", new=mock.AsyncMock(return_value=fake)
        ) as gs:
            result = await roblox_mod.RobloxStudio.connect()
        self.assertIs(result, fake)
        self.assertEqual(gs.await_count, 1)

    async def test_reconnects_when_dropped(self):
        dropped = _FakeStudio()
        dropped.client.is_connected = False
        roblox_mod._singleton = dropped
        fresh = _FakeStudio()
        with mock.patch.object(
            roblox_mod.RobloxStudio, "connect", new=mock.AsyncMock(return_value=fresh)
        ):
            result = await get_singleton()
        self.assertIs(result, fresh)
        self.assertIs(roblox_mod._singleton, fresh)


class TestServerProxy(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_returns_upstream_info(self):
        class FakeClient:
            protocol_version = "2024-11-05"
            capabilities = {"tools": {"listChanged": True}}
            server_info = {"name": "RobloxStudio", "version": "1.0.0"}

        sent = []
        with mock.patch.object(server_mod, "_send", side_effect=lambda m: sent.append(m)):
            await server_mod._handle_message(
                FakeClient(),
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
        self.assertEqual(sent[0]["result"]["serverInfo"]["name"], "RobloxStudio")

    async def test_tools_call_is_forwarded(self):
        class FakeClient:
            async def request(self, method, params):
                return {"content": [{"type": "text", "text": "ok"}]}

        sent = []
        with mock.patch.object(server_mod, "_send", side_effect=lambda m: sent.append(m)):
            await server_mod._handle_message(
                FakeClient(),
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "execute_luau", "arguments": {"code": "return 1"}},
                },
            )
        self.assertEqual(sent[0]["result"]["content"][0]["text"], "ok")


class TestPlatformDefaults(unittest.TestCase):
    def test_macos_uses_bundled_binary_directly_no_shell(self):
        from roblox_studio_mcp.roblox import (
            MACOS_COMMAND,
            platform_defaults,
        )

        command, args, shell = platform_defaults("darwin")
        self.assertEqual(command, MACOS_COMMAND)
        self.assertEqual(
            MACOS_COMMAND,
            "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP",
        )
        self.assertEqual(args, [])
        self.assertFalse(shell)

    def test_windows_keeps_cmd_mcp_bat_in_shell_mode(self):
        from roblox_studio_mcp.roblox import (
            WINDOWS_ARGS,
            WINDOWS_COMMAND,
            platform_defaults,
        )

        command, args, shell = platform_defaults("win32")
        self.assertEqual(command, "cmd.exe")
        self.assertEqual(list(args), list(WINDOWS_ARGS))
        self.assertTrue(shell)
        self.assertEqual(WINDOWS_COMMAND, "cmd.exe")

    def test_default_command_follows_platform(self):
        import sys as _sys

        import roblox_studio_mcp.roblox as roblox_mod
        from roblox_studio_mcp.roblox import (
            MACOS_COMMAND,
            WINDOWS_COMMAND,
            default_command,
            default_shell,
        )

        with mock.patch.object(_sys, "platform", "darwin"):
            self.assertEqual(default_command(), MACOS_COMMAND)
            self.assertFalse(default_shell())
        with mock.patch.object(_sys, "platform", "win32"):
            self.assertEqual(default_command(), WINDOWS_COMMAND)
            self.assertTrue(default_shell())


if __name__ == "__main__":
    unittest.main()
