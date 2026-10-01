"""The *shapes* of the leaf modules' return values, not just their prose.

The precedent is `test_watch_output.py`, which exists because a tool returned
`{"returned": 0}` on every call, reported `isError: false`, and passed a suite
whose only assertion on it was its description. A test that checks a tool
"works" without checking what comes back cannot tell a working tool from a
silently empty one.

So these tests do two things:

1. **Pin the keys.** Every return value crossing a function boundary in
   `waiting`, `skills`, `registry`, `grep`, `breakpoints`, `updater` and
   `writer` is asserted to have exactly the keys its TypedDict declares. A key
   renamed on one side and not the other fails here rather than in production.
2. **Assert the annotation is a TypedDict, not `Dict[str, Any]`.** A
   `Dict[str, Any]` return makes every key access legal, which is precisely what
   let the `WatchResult` bug ship. The annotation is the fix and the checker is
   only the backstop, so the annotation is what gets asserted.

`from __future__ import annotations` is in effect in every module under test, so
`typing.get_type_hints` is used rather than reading the raw string: the raw
annotation is the *string* "WaitVerdict", and comparing that to the class is a
test that cannot pass.
"""

import inspect
import json
import os
import sys
import tempfile
import typing
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import breakpoints, grep, registry, skills, updater, waiting, writer
from roblox_studio_mcp.extended.breakpoints import HIT_PREFIX
from roblox_studio_mcp.types import CallToolResult


def _is_typed_dict(annotation) -> bool:
    return isinstance(annotation, type) and typing.is_typeddict(annotation)


def _return_type(function):
    """The resolved return annotation, or None if it is absent."""
    return typing.get_type_hints(function).get("return")


def _eval_in(module, typed_dict):
    """Resolve a TypedDict's annotations against the module that declared it.

    `typing.get_type_hints` on the class alone leaves nested forward references
    unresolved, because the names they mention (`Literal`, `ResolvedEntry`) live
    in the declaring module's namespace, not the caller's.
    """
    return typing.get_type_hints(typed_dict, globalns=vars(module))


class FakeStudio:
    """Answers execute_luau with a canned string and records what it was sent."""

    def __init__(self, response="ok", console=()):
        self.response = response
        self.console = list(console)
        self.sent: list[dict] = []
        self.studio_id = "sid-1"
        self.reads: list[str] = []

    async def call(self, name, arguments=None):
        arguments = arguments or {}
        self.sent.append({"tool": name, **arguments})
        return CallToolResult.from_dict(
            {"content": [{"type": "text", "text": self.response}]}
        )

    async def script_read(self, target_file, **kwargs):
        self.reads.append(target_file)
        return CallToolResult.from_dict(
            {"content": [{"type": "text", "text": "1→local a = 1\n2→local b = 2"}]}
        )


class ReturnShapesArePinned(unittest.TestCase):
    """Each function below returns a TypedDict, and the checker must see it.

    This is the test that would have caught the shipped bug's *sibling*: a
    function annotated `Dict[str, Any]` passing any key it likes, because the
    annotation promises nothing.
    """

    CASES = [
        ("parse_probe returns ProbeResult", waiting.parse_probe, waiting.ProbeResult),
        ("wait_for returns WaitVerdict", waiting.wait_for, waiting.WaitVerdict),
        ("load_skills returns List[Skill]", skills.load_skills, None),
        ("get_skill returns Skill", skills.get_skill, skills.Skill),
        ("read_in_band_identity returns InBandIdentity|None", registry.read_in_band_identity, None),
        ("record returns RegistryEntry", registry.record, registry.RegistryEntry),
        ("resolve returns a discriminated union", registry.resolve, None),
        ("list_instances returns ListResult", registry.list_instances, registry.ListResult),
        ("extended_script_grep returns List[GrepHit]", grep.extended_script_grep, None),
        ("list_breakpoints returns List[Breakpoint]", breakpoints.list_breakpoints, None),
    ]

    def test_each_returns_something_better_than_a_bare_dict(self):
        for label, function, expected in self.CASES:
            with self.subTest(label):
                got = _return_type(function)
                self.assertIsNotNone(
                    got, f"{label} has no return annotation at all"
                )
                if expected is None:
                    # A list or a union: check the payload, not the wrapper.
                    args = typing.get_args(got)
                    payloads = [a for a in args if a is not type(None)]
                    self.assertTrue(
                        payloads, f"{label} resolved to {got!r}, which has no payload"
                    )
                    for payload in payloads:
                        self.assertTrue(
                            _is_typed_dict(payload),
                            f"{label}: payload {payload!r} is not a TypedDict",
                        )
                else:
                    self.assertIs(got, expected)

    def test_none_of_them_is_a_bare_dict_any(self):
        """The specific hole. Every one of these used to be `Dict[str, Any]`."""
        for label, function, _expected in self.CASES:
            with self.subTest(label):
                got = _return_type(function)
                self.assertNotEqual(
                    got, typing.Dict[str, typing.Any],
                    f"{label} is Dict[str, Any], which accepts any key",
                )

    def test_resolve_is_a_union_of_three_disjoint_arms(self):
        """`resolve` returns three genuinely different key sets.

        One dict with optional keys would let `result["match"]` type-check on a
        `not_found` reply that has no such key — the same mistake as
        `WatchResult`, one layer up. The union forces a branch on `status`.
        """
        got = _return_type(registry.resolve)
        arms = typing.get_args(got)
        self.assertEqual(len(arms), 3, f"expected three arms, got {arms!r}")

        # `status` is a forward reference, because these TypedDicts are declared
        # under `from __future__ import annotations` and they live in
        # registry's namespace rather than this test's. Evaluating each against
        # that module is the real resolution — comparing the raw strings would
        # pass even if the Literal were misspelled on both sides.
        statuses = {
            typing.get_args(_eval_in(registry, a)["status"])[0]
            for a in arms
        }
        self.assertEqual(statuses, {"ok", "not_found", "ambiguous"})

        # The arms must not share a payload key, or the union buys nothing.
        self.assertNotIn("match", _eval_in(registry, arms[1]))
        self.assertNotIn("candidates", _eval_in(registry, arms[0]))
        self.assertIn("known", _eval_in(registry, arms[1]))
        self.assertIn("candidates", _eval_in(registry, arms[2]))


class ProbeResultShape(unittest.IsolatedAsyncioTestCase):
    def test_every_reply_has_all_four_keys(self):
        """Including the fault arms. A missing `fault` key is what would make a
        compile error indistinguishable from a timeout."""
        for raw, expect_ok, expect_fault in [
            ("true", True, None),
            ("false", True, None),
            ("3", True, None),
            ("false\tERR\tbad syntax", False, "ERR"),
            ("false\tTHREW\truntime", False, "THREW"),
            ("", False, "THREW"),
        ]:
            with self.subTest(raw=raw):
                got = waiting.parse_probe(raw)
                self.assertEqual(
                    set(got), {"ok", "value", "fault", "detail"},
                    "the four keys are what wait_for branches on",
                )
                self.assertEqual(got["ok"], expect_ok)
                self.assertEqual(got["fault"], expect_fault)
                self.assertIsInstance(got["detail"], str)

    def test_value_is_none_exactly_when_the_probe_did_not_run(self):
        """`value` is the whole answer to "what did the condition return", so
        None must mean "nothing ran", never "it returned nothing"."""
        self.assertIsNone(waiting.parse_probe("false\tTHREW\tx")["value"])
        self.assertIsNone(waiting.parse_probe("")["value"])
        self.assertIsNotNone(waiting.parse_probe("true")["value"])


class WaitVerdictShape(unittest.IsolatedAsyncioTestCase):
    async def _verdict(self, replies, timeout=1.0):
        class Poller(FakeStudio):
            async def call(self, name, arguments=None):
                self.sent.append({"tool": name})
                text = replies[min(len(self.sent) - 1, len(replies) - 1)]
                return CallToolResult.from_dict(
                    {"content": [{"type": "text", "text": text}]}
                )

        got = await waiting.wait_for(Poller("true"), "true", timeout)
        return got

    async def test_all_eight_keys_are_always_present(self):
        """Empty values are None or [], never absent keys.

        A caller must not have to distinguish "no value observed" from "key
        missing".

        Was nine. `warning` was removed with the behaviour it existed for: a
        truthy non-boolean is now an `INVALID_ARGUMENT` rather than
        `satisfied: true` plus a caveat, so nothing could produce the field and
        it would have shipped as a permanent `null`.
        """
        for replies in (["true"], ["false", "false", "false", "false"]):
            with self.subTest(replies=replies):
                got = await self._verdict(replies)
                self.assertEqual(
                    set(got),
                    {
                        "satisfied", "timed_out", "last_value", "polls",
                        "timeout_seconds", "final_poll_seconds",
                        "settled_repeats", "poll_errors",
                    },
                )

    async def test_timed_out_is_the_exact_complement_of_satisfied(self):
        """These two must never disagree. A verdict claiming both is the sort of
        thing a caller reads as success."""
        got = await self._verdict(["false"] * 8)
        self.assertFalse(got["satisfied"])
        self.assertTrue(got["timed_out"])

    async def test_a_truthy_non_boolean_is_refused_not_reported_satisfied(self):
        """In Lua 0 and "" are both truthy, so "0" would satisfy the wait.

        It used to come back `satisfied: true` with a warning beside it, which
        asserts the very thing the caller asked about - and a warning next to a
        green result is a note nobody reads. Now it raises, and names the value.
        """
        with self.assertRaises(waiting.ToolError) as caught:
            await waiting.wait_for(_ConstantReplier("0"), "0", 1.0)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        message = str(caught.exception)
        self.assertIn("0", message)
        self.assertIn("truthy", message)

    async def test_nil_is_not_yet_rather_than_an_error(self):
        """The other direction, and the reason the rule is not simply
        "non-boolean means error".

        `return workspace.Foo` is an ordinary condition that yields nil, and
        hard-erroring it would break the tool's main use. Nil is falsy in Lua,
        so it must mean keep polling - not failure, and not success.
        """
        got = await waiting.wait_for(_ConstantReplier("nil"), "workspace.Foo", 0.2)
        self.assertFalse(got["satisfied"])
        self.assertTrue(got["timed_out"])
        self.assertEqual(got["last_value"], "nil")

    async def test_false_is_still_not_yet(self):
        got = await waiting.wait_for(_ConstantReplier("false"), "false", 0.2)
        self.assertFalse(got["satisfied"])

    async def test_true_is_satisfied_with_no_warning_key_to_check(self):
        """Guards the removal: nothing reintroduces a permanent-null field."""
        got = await waiting.wait_for(_ConstantReplier("true"), "true", 1.0)
        self.assertTrue(got["satisfied"])
        self.assertNotIn("warning", got)


class _ConstantReplier(FakeStudio):
    def __init__(self, text):
        super().__init__(text)

    async def call(self, name, arguments=None):
        self.sent.append({"tool": name})
        return CallToolResult.from_dict(
            {"content": [{"type": "text", "text": self.response}]}
        )


class SkillShape(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, description="d"):
        # The suffix is required: load_skills only reads `*.md`, and a skill
        # whose frontmatter `name` disagrees with its filename is an error, not
        # a skip. So the file and the declared name must be written together.
        with open(os.path.join(self.dir, name + ".md"), "w", encoding="utf-8") as handle:
            handle.write(
                "---\nname: %s\ndescription: %s\n---\n\n# %s\n\nbody\n"
                % (name, description, name)
            )

    def test_a_loaded_skill_has_exactly_six_keys(self):
        self._write("rsx-one")
        (skill,) = skills.load_skills(self.dir)
        self.assertEqual(
            set(skill),
            {"name", "description", "source", "path", "body", "content"},
        )

    def test_content_has_the_frontmatter_stripped_and_body_does_not(self):
        """The reason both keys exist. Shipping the frontmatter inside `content`
        is how a `description:` line ends up in the middle of a prompt."""
        self._write("rsx-one")
        (skill,) = skills.load_skills(self.dir)
        self.assertNotIn("description:", skill["content"])
        self.assertIn("description:", skill["body"])

    def test_the_two_call_skill_arms_carry_disjoint_keys(self):
        """Index and single-skill are different shapes, not one with holes."""
        self._write("rsx-one")
        _text, index_result = skills.call_skill(None, self.dir)
        self.assertEqual(set(index_result), {"skills"})
        self.assertNotIn("name", index_result)

        _text, detail = skills.call_skill("rsx-one", self.dir)
        self.assertEqual(set(detail), {"name", "description", "chars"})
        self.assertNotIn("skills", detail)

    def test_index_rows_carry_only_name_and_description(self):
        """The index is paid on every call of every session; a body smuggled in
        here is the cost this module exists to avoid."""
        self._write("rsx-one")
        _text, result = skills.call_skill(None, self.dir)
        for row in result["skills"]:
            self.assertEqual(set(row), {"name", "description"})


class RegistryShape(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.mkdtemp()
        self.path = os.path.join(directory, "studios.json")

    def _record(self, debug_id, name="A", studio_id=None):
        return registry.record(
            studio_id=studio_id or f"sid-{debug_id}",
            identity={
                "debug_id": debug_id, "name": name,
                "place_id": 0, "game_id": 0,
            },
            path=self.path,
        )

    def test_a_recorded_entry_has_exactly_eight_keys(self):
        self.assertEqual(
            set(self._record("0_1")),
            {
                "debug_id", "name", "place_id", "game_id",
                "last_studio_id", "last_seen", "studio_id_history", "id_changed",
            },
        )

    def test_identity_fields_are_none_rather_than_absent_without_an_identity(self):
        """`record` is routinely called with no identity at all (refresh failed).
        The keys must be present and null, so 'we looked and got nothing' stays
        distinguishable from 'this field does not apply'."""
        entry = registry.record(studio_id="sid-x", identity=None, path=self.path)
        for key in ("debug_id", "name", "place_id", "game_id"):
            with self.subTest(key=key):
                self.assertIn(key, entry)
                self.assertIsNone(entry[key])

    def test_recorded_entry_survives_a_json_round_trip(self):
        """It is written to disk and read back by a later process. A key that
        only existed in memory would be lost between them."""
        self._record("0_1")
        with open(self.path, encoding="utf-8") as handle:
            on_disk = json.load(handle)["instances"]["0_1"]
        self.assertEqual(set(on_disk), set(self._record("0_1")))

    def test_resolve_ok_carries_a_match_with_ten_keys(self):
        self._record("0_1", name="A")
        got = registry.resolve(debug_id="0_1", path=self.path)
        self.assertEqual(got["status"], "ok")
        self.assertEqual(
            set(got["match"]),
            {
                "debug_id", "name", "place_id", "game_id",
                "last_studio_id", "last_seen", "studio_id_history",
                "id_changed", "stale", "age_seconds",
            },
        )

    def test_resolve_not_found_has_no_match_key(self):
        """The bug the union exists to prevent: `result["match"]` on a miss."""
        self._record("0_1", name="A")
        got = registry.resolve(debug_id="nope", path=self.path)
        self.assertEqual(got["status"], "not_found")
        self.assertNotIn("match", got)
        self.assertIsInstance(got["reason"], str)
        self.assertEqual(len(got["known"]), 1)

    def test_resolve_ambiguous_has_candidates_and_no_match(self):
        self._record("0_1", name="A")
        self._record("0_2", name="B")
        got = registry.resolve(path=self.path)
        self.assertEqual(got["status"], "ambiguous")
        self.assertNotIn("match", got)
        self.assertEqual(len(got["candidates"]), 2)

    def test_a_stale_entry_is_flagged_not_hidden(self):
        import unittest.mock as mock

        with mock.patch.object(registry.time, "time", return_value=0.0):
            self._record("0_1", name="A")
        got = registry.resolve(debug_id="0_1", path=self.path)
        self.assertTrue(got["match"]["stale"])

    async def test_listed_instances_have_all_eight_keys_every_time(self):
        """Including when the identity read failed. The old code appended
        `registered` and `id_changed` in a branch after building the dict, so
        the row's key set depended on whether a refresh happened."""

        class Client:
            async def list_studios(self):
                return [{"id": "sid-1", "name": "A"}]

            @property
            def client(self):
                return self

        for refresh in (True, False):
            with self.subTest(refresh=refresh):
                got = await registry.list_instances(
                    Client(), refresh=refresh, path=self.path
                )
                self.assertEqual(
                    set(got),
                    {"version", "registry_path", "count", "instances",
                     "registered_total"},
                )
                (row,) = got["instances"]
                self.assertEqual(
                    set(row),
                    {"studio_id", "reported_name", "debug_id", "place_id",
                     "game_id", "identity_error", "registered", "id_changed"},
                )


class InBandIdentityShape(unittest.IsolatedAsyncioTestCase):
    async def test_the_four_parsed_fields_are_ints_not_console_text(self):
        class Studio(FakeStudio):
            async def call(self, name, arguments=None):
                if name == "get_console_output":
                    return CallToolResult.from_dict(
                        {"content": [{"type": "text",
                                      "text": "MCPID\t0_1\tPlace\t123\t456"}]}
                    )
                return await super().call(name, arguments)

        got = await registry.read_in_band_identity(Studio())
        self.assertEqual(
            got, {"debug_id": "0_1", "name": "Place", "place_id": 123, "game_id": 456}
        )
        self.assertIsInstance(got["place_id"], int)
        self.assertIsInstance(got["game_id"], int)

    async def test_a_non_numeric_place_id_reads_as_zero_not_as_text(self):
        """`game.PlaceId` is genuinely 0 for an unpublished place, so 0 is also
        the fallback. Documented on InBandIdentity: the digit check is a
        parse, not a validity signal."""

        class Studio(FakeStudio):
            async def call(self, name, arguments=None):
                if name == "get_console_output":
                    return CallToolResult.from_dict(
                        {"content": [{"type": "text",
                                      "text": "MCPID\t0_1\tPlace\tnope\t456"}]}
                    )
                return await super().call(name, arguments)

        got = await registry.read_in_band_identity(Studio())
        self.assertEqual(got["place_id"], 0)


class GrepHitShape(unittest.IsolatedAsyncioTestCase):
    async def test_a_hit_has_exactly_four_keys(self):
        studio = FakeStudio(
            json.dumps([{"path": "game.S.A", "line_number": 2, "excerpt": "local a = 1"}])
        )
        (hit,) = await grep.extended_script_grep(studio, "a")
        self.assertEqual(set(hit), {"path", "line_number", "excerpt", "context_lines"})

    async def test_excerpt_is_always_a_string(self):
        """The defect this type exposed.

        `line` is the first-choice alias for the line *number*, and it was also
        read as an excerpt alias. A server that sent only `{"line": 12}` put the
        int 12 in `excerpt` and, because 12 is truthy, skipped the source read
        that would have supplied the text. The output field is typed `str` and
        every consumer treats it as text.
        """
        studio = FakeStudio(json.dumps([{"path": "game.S.A", "line": 12}]))
        (hit,) = await grep.extended_script_grep(studio, "a")
        self.assertIsInstance(hit["excerpt"], str)
        self.assertNotEqual(hit["excerpt"], "12", "the line number leaked into excerpt")
        # And the number itself still came through, from the same field.
        self.assertEqual(hit["line_number"], 12)
        # The read that supplies real text must have happened.
        self.assertEqual(studio.reads, ["game.S.A"])

    async def test_a_hit_with_no_usable_path_is_dropped(self):
        studio = FakeStudio(json.dumps([{"line_number": 1, "excerpt": "x"}]))
        self.assertEqual(await grep.extended_script_grep(studio, "a"), [])

    async def test_a_non_string_path_is_dropped_not_forwarded(self):
        """A dict or number in the path position must not reach the output."""
        studio = FakeStudio(json.dumps([{"path": {"nested": 1}, "excerpt": "x"}]))
        self.assertEqual(await grep.extended_script_grep(studio, "a"), [])

    async def test_the_output_is_json_serialisable_as_declared(self):
        """The server does `json.dumps(hits)`. If a value were not a JSON type
        the tool would fail at the last step, after all the work."""
        studio = FakeStudio(json.dumps([{"path": "game.S.A", "line": 3}]))
        hits = await grep.extended_script_grep(studio, "a")
        self.assertEqual(json.loads(json.dumps(hits)), hits)


class BreakpointShape(unittest.IsolatedAsyncioTestCase):
    async def test_a_listed_breakpoint_has_exactly_three_keys(self):
        studio = FakeStudio('count=1 BpTest_L5|BpTest:5 = error("hit")')
        (bp,) = await breakpoints.list_breakpoints(studio)
        self.assertEqual(set(bp), {"script_path", "line", "log_expression"})

    async def test_set_breakpoint_reports_the_six_documented_keys(self):
        """`SetBreakpointResult` is declared but the function is still annotated
        `Dict[str, Any]`, because its only caller assigns into a `Dict[str, Any]`
        in extended_server.py. This test is what keeps the declared shape from
        drifting in the meantime."""
        got = await breakpoints.set_breakpoint(FakeStudio("ok"), "BpTest", 5)
        self.assertEqual(
            set(got), set(typing.get_type_hints(breakpoints.SetBreakpointResult))
        )
        self.assertEqual(got["verified"], True)
        self.assertEqual(got["continue_execution"], True)
        self.assertEqual(got["hit_prefix"], HIT_PREFIX)

    async def test_the_default_expression_is_a_deliberate_failure(self):
        """A *successful* log injection is invisible through the console, so the
        default has to be `error(...)` to be the reporting channel at all."""
        got = await breakpoints.set_breakpoint(FakeStudio("ok"), "BpTest", 5)
        self.assertIn("error(", got["log_expression"])


class WriterAndUpdaterShape(unittest.IsolatedAsyncioTestCase):
    async def test_write_status_is_one_of_exactly_three_strings(self):
        """`WriteStatus` is a Literal over the three returns. A caller that
        branches on the status must not be handed a fourth."""
        self.assertEqual(
            typing.get_args(writer.WriteStatus), ("wrote", "unchanged", "created")
        )
        self.assertEqual(_return_type(writer.write_script), writer.WriteStatus)

    async def test_unchanged_is_reported_without_writing(self):
        class Existing(FakeStudio):
            async def script_read(self, target_file, **kwargs):
                return CallToolResult.from_dict(
                    {"content": [{"type": "text", "text": "1→print(1)"}]}
                )

        studio = Existing()
        got = await writer.write_script(studio, "game.S.A", "print(1)")
        self.assertEqual(got, "unchanged")
        self.assertEqual(studio.sent, [], "an unchanged write must not call a tool")

    def test_the_multi_edit_wire_entry_has_exactly_two_keys(self):
        """`MultiEditEntry` is serialised straight into an outbound tool call, so
        a misspelled key would leave this process with nothing to catch it."""
        self.assertEqual(
            set(typing.get_type_hints(updater.MultiEditEntry)),
            {"old_string", "new_string"},
        )

    async def test_a_successful_batch_sends_exactly_that_wire_shape(self):
        class Existing(FakeStudio):
            async def script_read(self, target_file, **kwargs):
                return CallToolResult.from_dict(
                    {"content": [{"type": "text", "text": "1→alpha\n2→beta"}]}
                )

        studio = Existing()
        result = await updater.update_script(
            studio, "game.S.A", [("alpha", "gamma")]
        )
        self.assertEqual(result.updated, [0])
        call = next(c for c in studio.sent if c["tool"] == "multi_edit")
        for entry in call["edits"]:
            self.assertEqual(set(entry), {"old_string", "new_string"})


if __name__ == "__main__":
    unittest.main()
