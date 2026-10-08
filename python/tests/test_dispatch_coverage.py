"""Every extended tool answers through the real dispatch entry point.

A unit test that calls a handler (or the helper under it) cannot see a
missing ``await`` or an unregistered handler - that is how the relay guard
shipped dead while its unit tests passed. This drives ``_handle_message``,
which is what a client calls, once per tool in ``_EXTENDED_HANDLERS``, and
asserts a well-formed answer: one JSON-RPC response carrying a result, or an
error whose stable code is declared in ``ALL_CODES``.

For tools with required arguments the call goes in bare (``{}``), so the
handler's own ``_require_str`` fires with ``INVALID_ARGUMENT`` - reaching that
raise proves the handler body executed rather than merely existing in the
table. Tools that proceed without arguments run against a client double that
raises ``NO_STUDIO`` on any call, so they answer with a declared error instead
of touching a real Studio. Each dispatch is time-boxed: a hung handler fails
the test instead of wedging the suite.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "python", "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp import extended_server as es  # noqa: E402
from roblox_studio_mcp.extended.errors import ALL_CODES, NO_STUDIO, ToolError  # noqa: E402


class _NoStudioClient:
    """An MCPClient double with no Studio behind it.

    Every method raises ``NO_STUDIO`` - a declared code - so a handler that
    reaches past its argument guards answers with that error instead of
    touching the system. ``protocol_version`` and friends exist only because
    ``_handle_message`` reads them on adjacent paths, not this one.
    """

    protocol_version = "2024-11-05"
    capabilities = {}
    server_info = {"name": "dispatch-sweep-double"}
    disabled_tools = set()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def _raise(*args, **kwargs):
            raise ToolError(NO_STUDIO, "dispatch sweep double has no Studio")

        return _raise


# Tools whose handlers require arguments go in bare: the expected answer is
# the handler's own INVALID_ARGUMENT. The rest proceed far enough to hit the
# client double (or answer outright, like skill with no name).
_BARE_INVALID = [
    "extended_write_script",
    "extended_update_script",
    "extended_script_grep",
    "extended_script_search_and_read",
    "extended_insert_asset_from_file",
    "extended_execute_luau_from_file",
    "extended_wait_for",
    "extended_breakpoints",
]

_PROCEEDING = {
    # name: arguments that reach the handler body without hanging.
    "extended_watch_output": {},
    "extended_run_tests": {},
    "extended_list_studios": {"refresh": False},
    "extended_studio_identity": {},
    "extended_capture": {},
    "extended_manage_instance": {"action": "places"},
    "extended_skill": {},
    # Takes no required arguments: proceeds straight to session handling,
    # which answers NO_STUDIO against the double.
    "extended_clear_breakpoints": {},
}


class TestEveryToolAnswersThroughDispatch(unittest.TestCase):
    def _dispatch(self, name, arguments):
        sent = []
        with mock.patch.object(es, "_send", side_effect=lambda m: sent.append(m)):
            asyncio.new_event_loop().run_until_complete(
                asyncio.wait_for(
                    es._handle_message(
                        _NoStudioClient(),
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": arguments},
                        },
                    ),
                    timeout=20.0,
                )
            )
        self.assertEqual(len(sent), 1, f"{name}: one response, not several")
        return sent[0]

    def _assert_well_formed(self, name, response):
        self.assertEqual(response.get("jsonrpc"), "2.0", name)
        self.assertEqual(response.get("id"), 1, name)
        if "error" in response:
            code = response["error"].get("data", {}).get("code")
            self.assertIn(
                code,
                set(ALL_CODES),
                f"{name}: error code {code!r} is not declared",
            )
        else:
            self.assertIn("result", response, f"{name}: neither result nor error")

    def test_handler_table_covers_the_contract(self):
        """Static half: every contracted tool has a dispatch entry."""
        contracted = {tool.name for tool in es._EXTENDED_TOOLS}
        self.assertEqual(
            set(es._EXTENDED_HANDLERS),
            contracted,
            "handler table and tool surface disagree",
        )

    def test_bare_calls_reach_the_handler_body(self):
        """Dynamic half, part 1: missing required args fail *in* the handler."""
        for name in _BARE_INVALID:
            with self.subTest(tool=name):
                response = self._dispatch(name, {})
                self.assertIn("error", response, f"{name}: expected a refusal")
                code = response["error"].get("data", {}).get("code")
                self.assertEqual(
                    code,
                    "INVALID_ARGUMENT",
                    f"{name}: expected the handler's own refusal, got {code!r}",
                )

    def test_proceeding_calls_answer_cleanly(self):
        """Dynamic half, part 2: the rest answer without a real Studio."""
        for name, arguments in _PROCEEDING.items():
            with self.subTest(tool=name):
                self._assert_well_formed(name, self._dispatch(name, arguments))


if __name__ == "__main__":
    unittest.main()
