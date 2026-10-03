"""Tests for the wait loop, the probe wrapper, and error classification.

The probe wrapper gets the most attention because it had a real bug, twice.
First: wrapping the caller's condition in a Luau expression meant a parse
error faulted the tool's own frame, which wedged Studio's command execution
and surfaced as a 120 s client timeout. Second: an unparseable condition
wedged Studio-side execution persistently (measured 2026-09-30, twice), and
the poll's unbounded await wedged the sequential server loop with it until
reconnect. The fix is two parts: the condition is spliced as bare code inside
``pcall`` (no ``loadstring`` - it does not resolve in play mode), and each
poll's await is bounded, aborting after ``MAX_HUNG_POLLS`` consecutive hangs
with the restart named. These tests pin that, without needing a live Studio.
"""

import asyncio
import unittest

from roblox_studio_mcp.extended.errors import (
    ALL_CODES, AMBIGUOUS_STUDIO, CAPABILITY_DENIED, DATAMODAL_UNAVAILABLE,
    INVALID_ARGUMENT, LUA_ERROR, NO_STUDIO, NOT_FOUND, SIZE_LIMIT,
    STALE_STUDIO_ID, TEST_BUSY, TEST_REFUSED, TIMEOUT, ToolError, UNKNOWN,
    classify, is_lua_fault,
)
from roblox_studio_mcp.extended.waiting import (
    BACKOFF, MAX_HUNG_POLLS, MAX_POLL, MAX_WAIT, MIN_POLL, POLL_TIMEOUT,
    SETTLED_AFTER, VirtualClock, _luau_string, build_probe, parse_probe,
    wait_for,
)
from roblox_studio_mcp.types import CallToolResult


def fast_wait(studio, **kw):
    """`wait_for` on a virtual clock, so these tests cost no wall time.

    The wait's deadline and backoff are driven by the injected clock rather
    than by real elapsed time, which is the behaviour under test anyway -
    these tests assert *how many polls happened*, not how long they took.
    `poll_timeout` still has to be real: it bounds an await that never
    resolves, and cancelling that is `asyncio.wait_for`'s job.
    """
    clock = VirtualClock()
    kw.setdefault("poll_timeout", 0.01)
    return asyncio.run(
        wait_for(studio, "1 +", 90, clock=clock, sleep=clock.sleep, **kw)
    )


def fast_wait_raises(studio, **kw):
    try:
        return fast_wait(studio, **kw)
    except ToolError as exc:
        return exc


class TestProbeWrapper(unittest.TestCase):
    def test_emits_no_loadstring(self):
        """`loadstring` was removed, and this is the test that keeps it removed.

        It is not a matter of taste. `loadstring` does not resolve in play mode -
        user-confirmed, from a live `loadstring() is not available` in a script
        running under play. So a probe using it worked in Edit and faulted in
        Client/Server, which is the worst shape: a tool that advertises a
        `datamodel_type` it cannot honour. A technique that only exists in one of
        two supported contexts is a trap, not a technique.
        """
        for condition in ("1 + 1", "true", "1 +", '#Players:GetPlayers() >= 3'):
            self.assertNotIn("loadstring", build_probe(condition), condition)

    def test_wraps_the_condition_in_a_pcall_so_a_fault_is_data(self):
        """A condition that *faults* must come back as THREW, not crash the poll."""
        code = build_probe("error('x')")
        self.assertIn("pcall(function()", code)
        self.assertIn("THREW", code)

    def test_splices_the_condition_as_code_not_as_a_string_literal(self):
        """The bug this pins, and it is the worst kind: a silent false success.

        An earlier version kept `_luau_string()` and emitted
        `return ([[#game:GetDescendants() > 50]])`. A long-bracket string is a
        *literal*, so the probe returned the string
        `"#game:GetDescendants() > 50"` - which is truthy - and every wait reported
        `satisfied: true` on the first poll **without evaluating the condition**.
        Found live, because the verdict's non-boolean warning fired.

        The assertion that matters: the condition must appear as bare code. A
        `[[` before it means the probe is returning a string.
        """
        code = build_probe("#game:GetDescendants() > 50")
        self.assertIn("return (#game:GetDescendants() > 50)", code)
        self.assertNotIn("[[", code)
        self.assertNotIn("]]", code)

    def test_a_true_condition_is_code_not_a_literal(self):
        # The 'return (...)' has to be inside the function. Compiling the
        # caller's text as a statement rejected 'true', which is not a Luau
        # statement, so the first real condition failed to compile.
        self.assertIn("return (true)", build_probe("true"))

    def test_the_probe_evaluates_rather_than_echoes(self):
        """A structural check on the generated code, since a string comparison
        cannot tell an echo from an evaluation. If the condition is inside the
        function body, it runs; if it is inside a long-bracket literal, it is
        returned verbatim."""
        code = build_probe("#Players:GetPlayers() >= 3")
        body = code.split("pcall(function()", 1)[1]
        self.assertIn("#Players:GetPlayers() >= 3", body)
        # Nothing between the parens that could make it a literal.
        inner = body.split("return (", 1)[1].split(")", 1)[0]
        self.assertFalse(inner.strip().startswith("["), inner)

    def test_it_no_longer_claims_to_catch_parse_errors(self):
        """It cannot. Without `loadstring` there is no compile step to intercept,
        so the probe has no ERR branch - and a test that asserted one would be
        asserting a capability the code does not have. The hazard is documented in
        `PROBE_CAVEAT` instead of being papered over with an unreachable branch."""
        code = build_probe("1 +")
        self.assertNotIn("ERR", code)
        self.assertNotIn("if not chunk", code)

    def test_separates_syntax_from_runtime_faults(self):
        code = build_probe("error('x')")
        self.assertIn("THREW", code)

    def test_plain_value_uses_the_plain_bracket_form(self):
        # The bug: ']' was treated as a level indicator, so a value with none
        # produced '[]]', which does not parse.
        self.assertEqual(_luau_string("true"), "[[true]]")
        self.assertEqual(_luau_string(""), "[[]]")

    def test_never_emits_a_bracket_level(self):
        # Only '=' is a level indicator; '[]]' is not a valid opener.
        for text in ("true", "a", "]]", "x]y", "]", "]]]"):
            with self.subTest(text=text):
                literal = _luau_string(text)
                self.assertNotIn("]", literal[: literal.index("[", 1)])

    def test_only_a_double_bracket_forces_level_one(self):
        # A lone ']' is harmless at level 0: the string closes on the first
        # ']]', so ']]' alone is what needs level 1.
        for text in ("]", "x]", "a]b", "]x["):
            with self.subTest(text=text):
                self.assertEqual(_luau_string(text), "[[" + text + "]]")
        for text in ("a]]b", "]]", "]]]", "a]]b]]c"):
            with self.subTest(text=text):
                literal = _luau_string(text)
                self.assertTrue(literal.startswith("[=["), f"{text!r} -> {literal}")
                self.assertTrue(literal.endswith("]=]"), f"{text!r} -> {literal}")

    def test_equals_are_harmless_at_any_level(self):
        self.assertEqual(_luau_string("a=b"), "[[a=b]]")
        self.assertEqual(_luau_string("a]=b"), "[[a]=b]]")
        self.assertEqual(_luau_string("a]==b"), "[[a]==b]]")
        self.assertEqual(_luau_string("a]==]b"), "[[a]==]b]]")
        # a real ']]' adjacent in the value forces level 1
        self.assertEqual(_luau_string("a]]=b"), "[=[a]]=b]=]")
        self.assertEqual(_luau_string("a]=]b"), "[[a]=]b]]")

    def test_escapes_are_unnecessary_and_omitted(self):
        literal = _luau_string('say "hi" \\ and \n newline')
        self.assertIn('say "hi" \\ and \n newline', literal)

    def test_escapes_are_unnecessary_and_omitted(self):
        literal = _luau_string('say "hi" \\ and \n newline')
        self.assertIn('say "hi" \\ and \n newline', literal)


class TestParseProbe(unittest.TestCase):
    def test_plain_true(self):
        got = parse_probe("true")
        self.assertTrue(got["ok"])
        self.assertEqual(got["value"], "true")
        self.assertIsNone(got["fault"])

    def test_plain_false(self):
        got = parse_probe("false")
        self.assertTrue(got["ok"], "false is a successful evaluation")
        self.assertEqual(got["value"], "false")

    def test_numeric_value(self):
        got = parse_probe("3")
        self.assertTrue(got["ok"])
        self.assertEqual(got["value"], "3")

    def test_syntax_fault(self):
        got = parse_probe("false\tERR\tunexpected symbol near '1 +'")
        self.assertFalse(got["ok"])
        self.assertEqual(got["fault"], "ERR")
        self.assertIn("unexpected symbol", got["detail"])

    def test_runtime_fault(self):
        got = parse_probe("false\tTHREW\tscript is not a valid member")
        self.assertFalse(got["ok"])
        self.assertEqual(got["fault"], "THREW")

    def test_empty_reply_is_a_fault_not_a_pass(self):
        # An empty answer must never read as a satisfied condition.
        got = parse_probe("")
        self.assertFalse(got["ok"])
        self.assertIsNotNone(got["fault"])


class TestErrorClassification(unittest.TestCase):
    def test_known_engine_messages(self):
        cases = {
            "No Roblox Studio instances are connected. Ask the user": NO_STUDIO,
            "The requested studio_id is not connected": STALE_STUDIO_ID,
            "Place is not open": STALE_STUDIO_ID,
            "Edit datamodel is not available in Play mode": DATAMODAL_UNAVAILABLE,
            "'ProcessService' is not a valid Service name": DATAMODAL_UNAVAILABLE,
            "Failed to start the test because a previous one is still in progress.": TEST_BUSY,
            "AddPlayers: can only be called from the server DataModel": TEST_REFUSED,
            "bad allocation": SIZE_LIMIT,
            "Timed out waiting for 'tools/call'": TIMEOUT,
            "Could not find any instances at path 'game.X'": NOT_FOUND,
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(classify(RuntimeError(message)).code, expected)

    def test_luau_faults_are_distinct_from_api_refusals(self):
        # These are the caller's bug, not the engine saying no, and conflating
        # them is what made a failed probe look like a transport problem.
        for message in (
            "attempt to call a nil value (method 'Foo')",
            "attempt to index nil with 'Bar'",
            "X is not a valid member of Y",
            "Failed to parse command code",
            "Unable to cast Array to int",
        ):
            with self.subTest(message=message):
                self.assertEqual(classify(RuntimeError(message)).code, LUA_ERROR)
                self.assertTrue(is_lua_fault(message))

    def test_capability_denial(self):
        got = classify(RuntimeError("requires the RobloxScript capability"))
        self.assertEqual(got.code, CAPABILITY_DENIED)

    def test_ambiguity_is_recognised(self):
        got = classify(ToolError(AMBIGUOUS_STUDIO, "3 Studios connected", candidates=[1, 2]))
        self.assertEqual(got.code, AMBIGUOUS_STUDIO)
        self.assertEqual(got.data["candidates"], [1, 2])

    def test_unrecognised_falls_through_to_unknown(self):
        # Forcing everything into a bucket would be worse than admitting we do
        # not know what it is.
        self.assertEqual(classify(RuntimeError("something new entirely")).code, UNKNOWN)

    def test_tool_error_passes_through_untouched(self):
        original = ToolError(SIZE_LIMIT, "too big", limit=10)
        self.assertIs(classify(original), original)

    def test_every_code_is_declared(self):
        self.assertIn(UNKNOWN, ALL_CODES)
        self.assertIn(LUA_ERROR, ALL_CODES)

    def test_unknown_code_is_rejected_at_construction(self):
        with self.assertRaises(AssertionError):
            ToolError("NOT_A_REAL_CODE", "nope")

    def test_error_serialises_with_code_and_data(self):
        payload = ToolError(AMBIGUOUS_STUDIO, "pick one", candidates=[7]).to_error()
        self.assertEqual(payload["code"], AMBIGUOUS_STUDIO)
        self.assertEqual(payload["data"]["candidates"], [7])

    def test_omits_data_when_there_is_none(self):
        self.assertNotIn("data", ToolError(TIMEOUT, "slow").to_error())


class TestPollingConstants(unittest.TestCase):
    def test_backoff_is_bounded_and_ordered(self):
        self.assertLess(MIN_POLL, MAX_POLL)
        self.assertGreater(BACKOFF, 1.0)
        self.assertGreater(SETTLED_AFTER, 1)

    def test_wait_cap_is_below_the_client_timeout(self):
        # 120 is the client default; exceeding it means being cut off with no
        # diagnostic rather than getting a clean timeout verdict.
        self.assertLess(MAX_WAIT, 120.0)

    def test_a_wait_stays_inside_the_client_timeout(self):
        # The budget is a target, not a guarantee: the last poll can start just
        # before expiry and then run long. So budget + worst-case poll must fit
        # inside the 120 s client timeout, or the caller gets an opaque error
        # instead of a verdict. A poll is a single execute_luau, bounded by
        # POLL_TIMEOUT - pinned against the constant, not a copy of it, so a
        # raise of the bound fails here instead of silently overrunning.
        CLIENT_TIMEOUT = 120.0
        self.assertLessEqual(MAX_WAIT + POLL_TIMEOUT, CLIENT_TIMEOUT)

    def test_a_launch_wait_also_fits(self):
        # Same reasoning for the launch path, which blocks on a tool call. The
        # constant lives with the launcher now, since trimming launch's arguments
        # moved it there from the server module.
        from roblox_studio_mcp.extended.instance import LAUNCH_WAIT

        self.assertLessEqual(LAUNCH_WAIT + 30.0, 120.0)


class _ScriptedStudio:
    """A studio that replays a script: "hang" never replies, anything else is
    the probe's text reply. Counts calls so the test can prove the wait
    stopped polling rather than merely stopped waiting."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def call(self, name, arguments=None):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if step == "hang":
            await asyncio.sleep(3600)
        return CallToolResult(content=[{"type": "text", "text": step}])


class TestHungPolls(unittest.TestCase):
    """Measured 2026-09-30, twice: an unparseable condition wedged Studio-side
    command execution, the poll's await never resolved, and the sequential
    server loop wedged with it until the MCP was reconnected. These pin the
    bound without needing a live Studio: the fake hangs the same way (an
    await that never resolves) and the test proves the wait aborts instead of
    hanging with it."""

    def test_consecutive_hangs_abort_with_the_restart(self):
        studio = _ScriptedStudio(["hang"])
        err = fast_wait_raises(studio)
        self.assertEqual(err.code, TIMEOUT)
        self.assertIn("never executed", err.message)
        self.assertIn("Restart the Studio", err.message)
        self.assertEqual(studio.calls, MAX_HUNG_POLLS)

    def test_a_reply_resets_the_hang_count(self):
        # hang, hang, reply, hang, hang, hang: the reply in the middle proves
        # Studio executed something, so the count restarts and the abort comes
        # three hangs later - six polls total, not three.
        studio = _ScriptedStudio(["hang", "hang", "false", "hang", "hang", "hang"])
        self.assertEqual(fast_wait_raises(studio).code, TIMEOUT)
        self.assertEqual(studio.calls, 6)

    def test_a_single_hang_does_not_abort_a_healthy_wait(self):
        # One hang could be a slow Studio. The wait absorbs it as a poll error
        # and a healthy verdict still lands.
        studio = _ScriptedStudio(["hang", "false", "true"])
        verdict = fast_wait(studio)
        self.assertTrue(verdict["satisfied"])
        self.assertEqual(verdict["polls"], 3)
        self.assertEqual(len(verdict["poll_errors"]), 1)

    def test_the_injected_clock_still_honours_the_budget(self):
        """The seam must not become a way to skip the deadline.

        A virtual clock that does not advance, or a wait that ignores it, would
        let this pass while the real 90-second budget stopped being enforced.
        """
        clock = VirtualClock()
        studio = _ScriptedStudio(["false"])
        verdict = asyncio.run(
            wait_for(studio, "1 +", MAX_WAIT, clock=clock, sleep=clock.sleep)
        )
        self.assertFalse(verdict["satisfied"])
        self.assertGreaterEqual(clock.now, MAX_WAIT)
        self.assertLess(clock.now, MAX_WAIT + MAX_POLL)


if __name__ == "__main__":
    unittest.main()
