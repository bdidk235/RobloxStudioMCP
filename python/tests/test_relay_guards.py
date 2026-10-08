"""P0.1: `screen_capture` must not silently capture the wrong Studio.

The gap this closes was mis-stated for weeks in this repo. `screen_capture` is a
*relayed* Studio tool, and the standing note said that therefore we "cannot add
parameters to it" - which is true of the tool's implementation and irrelevant to
whether the **proxy** can look at the arguments before forwarding. The request
arrives here first. The proxy holds `name` and `arguments` on every relayed call.

So the tool is forwarded untouched when the target is unambiguous, and refused
when it is not. Never guessed.

Measured failure this prevents: probes ran against one place while every capture
came back as an empty baseplate, because a second Studio was attached and sorted
first. The image was valid, correctly sized and of the wrong place - and
expensive to diagnose from the pixels, because it reads as a compositing bug.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended_server import (  # noqa: E402
    _RELAY_GUARDS,
    _guard_screen_capture,
)
from roblox_studio_mcp.extended.errors import AMBIGUOUS_STUDIO, ToolError  # noqa: E402


class FakeClient:
    """Answers `list_roblox_studios`.

    Returns a real `CallToolResult` rather than a bare dict: `RobloxStudio
    .list_studios()` calls `.json()` on it, so a dict-shaped fake fails for a
    reason that has nothing to do with the guard under test.
    """

    def __init__(self, studios, fail: bool = False):
        self.studios = studios
        self.fail = fail
        self.called = []

    async def call_tool(self, name, arguments=None):
        self.called.append(name)
        if self.fail:
            raise RuntimeError("mesh unreachable")
        import json

        from roblox_studio_mcp.types import CallToolResult

        return CallToolResult(
            content=[{"type": "text", "text": json.dumps(self.studios)}]
        )


class ScreenCaptureGuard(unittest.TestCase):
    async def _run(self, client, args):
        from roblox_studio_mcp.extended_server import _RELAY_GUARD_NOTES

        _RELAY_GUARD_NOTES.clear()
        await _guard_screen_capture(client, args)
        return list(_RELAY_GUARD_NOTES)


class OneStudio(ScreenCaptureGuard):
    def test_passes_through_when_unambiguous(self):
        import asyncio

        client = FakeClient([{"id": "sid-a", "name": "Place1"}])
        asyncio.run(self._run(client, {}))  # must not raise

    def test_an_explicit_studio_id_is_never_second_guessed(self):
        import asyncio

        client = FakeClient(
            [{"id": "a", "name": "One"}, {"id": "b", "name": "Two"}]
        )
        # Even with two attached, an explicit id is the caller having disambiguated.
        asyncio.run(self._run(client, {"studio_id": "b"}))


class SeveralStudios(ScreenCaptureGuard):
    def test_refuses_and_names_the_candidates(self):
        import asyncio

        client = FakeClient(
            [{"id": "a", "name": "One"}, {"id": "b", "name": "Two"}]
        )
        with self.assertRaises(ToolError) as caught:
            asyncio.run(self._run(client, {}))
        err = caught.exception
        self.assertEqual(err.code, AMBIGUOUS_STUDIO)
        self.assertEqual(
            [c["studio_id"] for c in err.data["candidates"]], ["a", "b"]
        )

    def test_the_message_says_what_to_do_instead(self):
        import asyncio

        client = FakeClient([{"id": "a", "name": "One"}, {"id": "b", "name": "Two"}])
        with self.assertRaises(ToolError) as caught:
            asyncio.run(self._run(client, {}))
        message = caught.exception.message
        self.assertIn("studio_id", message)
        self.assertIn("extended_capture", message)
        # Naming *why* an omitted id has no answer is what separates this from a
        # generic ambiguity refusal. The old wording ("whichever one the mesh
        # returns first") asserted an optional-id mechanism that was measured
        # false on 2026-10-01: the schema requires studio_id, so an omitted one
        # is refused before dispatch. The guard stays as defence-in-depth for
        # callers that skip schema validation.
        self.assertIn("no single right answer", message)


class MeshUnavailable(ScreenCaptureGuard):
    def test_does_not_block_a_maybe_unambiguous_call(self):
        """Our own failure must not become a refusal.

        A transport hiccup here would otherwise read as a policy decision, and
        the caller would go looking for ambiguity that may not exist. Pass the
        call through, and say the check could not run.
        """
        import asyncio

        client = FakeClient([], fail=True)
        notes = asyncio.run(self._run(client, {}))
        self.assertEqual(len(notes), 1)
        self.assertIn("passing through", notes[0])

    def test_no_studios_is_not_ambiguity(self):
        import asyncio

        client = FakeClient([])
        asyncio.run(self._run(client, {}))  # must not raise


class TheGuardIsRegistered(unittest.TestCase):
    def test_screen_capture_has_a_guard(self):
        self.assertIn("screen_capture", _RELAY_GUARDS)

    def test_every_guard_target_is_a_relayed_tool_not_an_extended_one(self):
        """Guarding an extended tool would be two mechanisms for one thing -
        those already refuse unknown arguments before dispatch."""
        from roblox_studio_mcp.extended_server import _EXTENDED_HANDLERS

        for name in _RELAY_GUARDS:
            self.assertNotIn(name, _EXTENDED_HANDLERS, name)
            self.assertFalse(
                name.startswith("extended_"),
                f"{name} is ours; it does not need a relay guard",
            )


class UnknownArgumentsPassThroughOnRelayedTools(unittest.TestCase):
    """The refusal is scoped to the extended table, not to unknown arguments.

    `_reject_unknown_arguments` runs only inside the `name in
    _EXTENDED_HANDLERS` branch, so EVERY relayed tool skips it - not just
    `screen_capture`. This pins that scope with a second relayed tool, so a
    later reader cannot conclude the exemption is per-tool.
    """

    def test_execute_luau_forwards_unknown_arguments_untouched(self):
        import asyncio
        from unittest import mock

        import roblox_studio_mcp.extended_server as es

        seen = {}

        class RecordingClient:
            async def request(self, method, params):
                seen["method"] = method
                seen["params"] = params
                return {"content": [{"type": "text", "text": "ok"}]}

        sent = []
        with mock.patch.object(es, "_send", side_effect=lambda m: sent.append(m)):
            asyncio.new_event_loop().run_until_complete(
                es._handle_message(
                    RecordingClient(),
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "tools/call",
                        "params": {
                            "name": "execute_luau",
                            "arguments": {
                                "code": "return 1",
                                "datamodel_type": "Edit",
                                "not_a_real_argument": True,
                            },
                        },
                    },
                )
            )
        self.assertEqual(len(sent), 1, "one response, not several")
        self.assertIn("result", sent[0], sent[0].get("error"))
        # Untouched means untouched: the bogus key is still there, because
        # this proxy cannot add parameters to Roblox's tools - or remove them.
        self.assertTrue(
            seen["params"]["arguments"].get("not_a_real_argument"),
            seen["params"],
        )


if __name__ == "__main__":
    unittest.main()