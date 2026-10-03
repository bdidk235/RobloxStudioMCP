"""Unknown tool parameters must be refused, not silently dropped (request P0.2a).

**This is the single highest-value check in the server**, and these tests exist
because of what it prevents rather than what it reports.

A silently-ignored parameter produces a *plausible wrong answer* rather than an
error, and this project has three separate instances of that, all of which
reported success:

* ``screen_capture`` accepted ``format: "png"`` and returned JPEG. Eight
  parameter names were tried before anyone noticed, because none of them errored.
* A past ``extended_watch_output`` variant had no ``pattern``, so passing it
  returned the whole console buffer - which reads identically to "no breakpoint
  was hit".
* ``max_lines: 0`` became 200 through a falsy default.

The contract in ``contract/tools.json`` pins the accepted arguments, so the
same call behaves the same however the server is configured.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp import extended_server as es  # noqa: E402


class UnknownArgumentsRejected(unittest.TestCase):
    def _reject(self, tool, **arguments):
        return es._reject_unknown_arguments(tool, arguments)  # noqa: SLF001

    def test_a_declared_argument_is_accepted(self):
        self.assertIsNone(self._reject("extended_watch_output", pattern="^x", max_lines=5))

    def test_no_arguments_is_accepted(self):
        self.assertIsNone(self._reject("extended_watch_output"))

    def test_an_unknown_argument_is_refused(self):
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_watch_output", patttern="^x")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_the_error_names_the_tool_and_the_offender(self):
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_capture", format="png")
        message = caught.exception.message
        self.assertIn("extended_capture", message)
        self.assertIn("format", message)

    def test_the_error_lists_what_is_accepted(self):
        """A bare "unknown key" sends the caller hunting. The message has to say
        what the tool does accept."""
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_capture", nope=1)
        self.assertIn("save_path", caught.exception.message)

    def test_a_near_miss_gets_a_suggestion(self):
        """The common cause is a spelling variant, and the message has to name it
        or the caller is left hunting.

        Note `replaceAll` is *not* the example here: it lives inside each entry of
        the `edits` array, not as a top-level property, so there is nothing at
        this level to suggest. The handler accepts that alias deliberately, in its
        own namespace. An earlier version of this test used it and asserted a
        suggestion that could not exist.
        """
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_breakpoints", line_number=3)
        self.assertIn("did you mean", caught.exception.message)
        self.assertIn("line", caught.exception.message)

    def test_a_differently_spelled_path_gets_a_suggestion(self):
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_capture", savepath="/tmp/a.png")
        self.assertIn("did you mean", caught.exception.message)
        self.assertIn("save_path", caught.exception.message)

    def test_a_wildly_wrong_name_gets_no_suggestion(self):
        """A wrong suggestion is worse than none, so a low match returns nothing
        rather than guessing."""
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_capture", zzzzqqqxyzzy=1)
        self.assertNotIn("did you mean", caught.exception.message)

    def test_every_unknown_is_reported_not_just_the_first(self):
        """Fixing one typo and hitting the next is a worse loop than being told
        all of them at once."""
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_capture", alpha=1, beta=2)
        self.assertIn("alpha", caught.exception.message)
        self.assertIn("beta", caught.exception.message)

    def test_the_error_carries_machine_readable_data(self):
        with self.assertRaises(es.ToolError) as caught:
            self._reject("extended_capture", nope=1)
        payload = caught.exception.to_error()
        self.assertEqual(payload["data"]["tool"], "extended_capture")
        self.assertEqual(payload["data"]["unknown"], ["nope"])

    def test_an_unknown_tool_is_not_validated(self):
        """Only tools we serve have schemas. Anything else is relayed to Studio,
        which has its own rules, and refusing here would break the passthrough."""
        self.assertIsNone(self._reject("screen_capture", format="png"))


class TheScreenCaptureIncident(unittest.TestCase):
    """The concrete case from the request, pinned so it cannot come back.

    ``screen_capture`` is a *relayed* Studio tool, so this project cannot add
    parameters to it and `format: "png"` is still silently ignored - that is
    documented, not fixed. What is fixed is that our own tools never do this.
    """

    def test_a_relayed_tool_is_left_alone(self):
        self.assertIsNone(es._reject_unknown_arguments("screen_capture", {"format": "png"}))

    def test_our_own_tools_are_not_relayed(self):
        self.assertIn("extended_capture", es._EXTENDED_HANDLERS)
        with self.assertRaises(es.ToolError):
            es._reject_unknown_arguments("extended_capture", {"format": "png"})


class NoAcceptedArgumentIsItselfADefault(unittest.TestCase):
    """An allowlisted exception must be a decision, not an accumulation.

    ``_IGNORED_ARGUMENTS`` exists so a client can send a harmless extra key
    without breaking. It is empty, and an entry must state why - otherwise the
    next exception quietly becomes the default.
    """

    def test_every_allowlisted_argument_declares_a_tool(self):
        for tool in es._IGNORED_ARGUMENTS:  # noqa: SLF001
            self.assertIn(tool, es._EXTENDED_HANDLERS, tool)

    def test_allowlisted_arguments_are_still_in_the_schema(self):
        """Allowlisting something the schema omits would hide the drift the
        parity test exists to catch."""
        for tool, keys in es._IGNORED_ARGUMENTS.items():  # noqa: SLF001
            declared = es._TOOL_PROPERTIES.get(tool, frozenset())  # noqa: SLF001
            for key in keys:
                self.assertIn(key, declared, "%s.%s" % (tool, key))

    def test_no_tool_allows_anything_at_the_moment(self):
        self.assertEqual(
            es._IGNORED_ARGUMENTS,  # noqa: SLF001
            {},
            "an argument is being silently accepted; state why, or remove it",
        )


class DispatchRefusesBeforeRunning(unittest.TestCase):
    """The check must run *before* the handler, not after.

    An unknown parameter plus a handler that has already acted on real Studio
    state is the worst ordering: the side effect happens and then the call is
    reported as rejected.
    """

    def test_the_handler_is_never_invoked(self):
        ran = []

        async def handler(client, arguments):
            ran.append(arguments)
            return {"content": [{"type": "text", "text": "{}"}]}

        with mock.patch.dict(es._EXTENDED_HANDLERS, {"extended_capture": handler}):
            with self.assertRaises(es.ToolError):
                es._reject_unknown_arguments("extended_capture", {"format": "png"})
        self.assertEqual(ran, [], "handler ran despite a rejected argument")

    def test_the_refusal_is_a_tool_error_not_a_crash(self):
        """It has to survive the existing except-and-classify path in the relay
        loop, which is what turns it into a JSON-RPC error rather than a
        traceback."""
        with self.assertRaises(es.ToolError) as caught:
            es._reject_unknown_arguments("extended_capture", {"nope": 1})
        payload = caught.exception.to_error()
        self.assertEqual(payload["code"], "INVALID_ARGUMENT")
        json.dumps(payload)  # must be serialisable


class UnknownBeatsMissing(unittest.TestCase):
    """A typo'd key that *is* the required key must be reported as a typo.

    **Measured live, and the interesting half is what was NOT the server's
    fault.** Sending ``root_paths`` (not ``root_path``) came back as::

        Invalid arguments ...: - root_path: Missing key

    ...with the typo never named. That error was raised by the *harness*, which
    validates a JSON Schema ``required`` list before the request reaches this
    process. Checked from the other end: ``max_resultz``, an unknown *optional*
    key so it survives the harness, does reach the server and is refused by
    name. And this project's own ``client.py`` performs no schema validation at
    all, so it is not reproducing the problem either.

    The server already orders this correctly. This test exists to keep it that
    way, and the docstring is the evidence for why that is worth pinning: the
    symptom is real, it is just not reachable from here.
    """

    TOOL = "extended_script_search_and_read"

    def test_a_typo_of_a_required_key_is_called_a_typo(self):
        with self.assertRaises(es.ToolError) as caught:
            es._reject_unknown_arguments(self.TOOL, {"root_paths": "game"})
        self.assertIn("root_paths", str(caught.exception))

    def test_the_required_key_really_is_missing_from_that_payload(self):
        """Guard the premise. If this stops being true the test above is
        asserting something vacuous."""
        schema = next(
            tool.input_schema for tool in es._EXTENDED_TOOLS if tool.name == self.TOOL
        )
        self.assertIn("root_path", schema["required"])
        self.assertNotIn("root_paths", schema.get("properties", {}))

    def test_a_genuinely_absent_key_is_still_a_missing_key(self):
        """The other direction: no typo present, so nothing to name, and the
        handler's own check must still fire."""
        es._reject_unknown_arguments(self.TOOL, {"studio_id": "abc"})
        with self.assertRaises(es.ToolError) as caught:
            es._require_str({"studio_id": "abc"}, "root_path")
        self.assertIn("root_path", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
