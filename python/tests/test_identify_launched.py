"""The console write in identity resolution must stay a genuine last resort.

`_identify_launched` answers "which mesh entry is the process I just started".
It has a cheap route - the process's own log states how it was launched, and that
is compared against what the mesh reports - and an expensive one that **prints a
token into a Studio's console**.

The expensive one used to be sprayed: it walked the mesh rows in whatever order
the bridge returned and printed into every Studio until one matched. With N
Studios attached that is N console lines, written into a user's live session, to
answer a question the cheap route had already failed to answer.

**What the change actually delivers, stated honestly:** the writes are now
*counted and reported*, so "asked one Studio" and "sprayed four and got nothing"
are distinguishable from outside. It does **not** reduce how many prints a failed
attempt costs. An earlier version of this fix also reordered the fallback by
evidence and claimed that cut the common case to a single print; the test written
to pin that claim disproved it, because the cheap branch already answers the one
case where the ordering would have mattered. The ordering is kept - it is correct
and free - and the code comment says plainly that it is inert.

So the tests below pin three things: the cheap route wins outright when it can,
the fallback still exists, and every path reports what the fallback cost.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional
from unittest import mock

from roblox_studio_mcp.extended import instance as inst


class _Studio:
    """Minimal client: `_identify_launched` only needs `list_studios`."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    async def list_studios(self) -> List[Dict[str, Any]]:
        return self._rows


def _rows(*pairs: tuple) -> List[Dict[str, Any]]:
    return [{"id": sid, "name": name} for sid, name in pairs]


class ConsoleWritesAreALastResort(unittest.IsolatedAsyncioTestCase):
    """The cheap route must win, and the fallback must report what it cost.

    The first test below is the one that matters: when the log route can single
    out a mesh row, **nothing is printed at all**. Two of these tests exist to
    pin a claim that turned out to be false - that ordering the fallback by
    evidence cuts the print count - so they assert the cheap route pre-empts it
    instead. See the comment in `_identify_launched`.
    """

    def setUp(self) -> None:
        self.asked: List[str] = []

    def _patched(self, *, identity, matches):
        """Stub the log route and the token route, and record who gets asked."""

        async def token(client, studio_id, candidates):
            self.asked.append(studio_id)
            target = matches.get(studio_id)
            if target is None:
                return {"resolved": False, "error": "no match"}
            return {"resolved": True, "pid": target, "how": "console token"}

        return mock.patch.multiple(
            inst,
            logid=mock.Mock(
                live_identities=lambda pids, started=None: {pids[0]: identity}
                if identity
                else {},
                name_matches_identity=lambda name, ident: bool(
                    name and ident and name == ident.get("place_path")
                ),
            ),
            _resolve_by_console_token=token,
        )

    async def test_a_single_log_match_prints_nothing(self):
        """The whole point of a cheap route: one log read, zero console writes.

        This is also why the fallback's ordering is inert. Exactly one match is
        the case where ordering would have mattered, and this branch answers it.
        """
        rows = _rows(("sid-wrong", "Other.rbxl"), ("sid-right", "Mine.rbxl"))
        identity = {"place_path": "Mine.rbxl", "task": "EditFile"}

        with self._patched(identity=identity, matches={"sid-right": 4242}):
            got = await inst._identify_launched(_Studio(rows), 4242, "Mine.rbxl")

        self.assertEqual(got["studio_id"], "sid-right")
        self.assertEqual(self.asked, [], "printed despite the log route answering")
        self.assertIn("no console write", got["how"])

    async def test_an_exactly_one_match_is_not_reached_via_the_fallback(self):
        """Guards the test above against being satisfied by accident. If the
        cheap branch ever stopped firing, this would catch it, because the
        fallback would answer the same question and report differently."""
        rows = _rows(("sid-wrong", "Other.rbxl"), ("sid-right", "Mine.rbxl"))
        identity = {"place_path": "Mine.rbxl", "task": "EditFile"}

        with self._patched(identity=identity, matches={"sid-right": 4242}):
            got = await inst._identify_launched(_Studio(rows), 4242, "Mine.rbxl")

        self.assertEqual(got["console_writes"], 0)

    async def test_two_matches_fall_through_and_are_counted(self):
        """The real fallback case, and it costs a print per candidate.

        Asserted as two, not one. The count is the honest part: the ordering does
        not reduce it, and the code comment says so. ``allow_console_write`` is
        required because the fallback is no longer the default.
        """
        rows = _rows(("a", "Mine.rbxl"), ("b", "Mine.rbxl"))
        identity = {"place_path": "Mine.rbxl", "task": "EditFile"}

        with self._patched(identity=identity, matches={"b": 9}):
            got = await inst._identify_launched(
                _Studio(rows), 9, "Mine.rbxl", allow_console_write=True
            )

        self.assertEqual(got["studio_id"], "b")
        self.assertEqual(got["console_writes"], 2)
        self.assertEqual(self.asked, ["a", "b"])

    async def test_no_evidence_still_falls_back_rather_than_guessing(self):
        """The fallback is not removed. Refusing here would be a regression: an
        unresolved answer is honest, a wrong one terminates the wrong Studio."""
        rows = _rows(("a", "One.rbxl"), ("b", "Two.rbxl"))

        with self._patched(identity={"place_path": "Nowhere.rbxl"}, matches={"b": 9}):
            got = await inst._identify_launched(
                _Studio(rows), 9, "Nowhere.rbxl", allow_console_write=True
            )

        self.assertEqual(got["studio_id"], "b")

    async def test_nothing_resolving_reports_the_writes_it_cost(self):
        """So "asked one Studio" and "sprayed four and got nothing" are
        distinguishable from outside."""
        rows = _rows(("a", "One.rbxl"), ("b", "Two.rbxl"))

        with self._patched(identity=None, matches={}):
            got = await inst._identify_launched(
                _Studio(rows), 5, "Nope.rbxl", allow_console_write=True
            )

        self.assertIsNone(got["studio_id"])
        self.assertEqual(got["console_writes"], 2)
        self.assertIn("no mesh entry", got["how"])

    async def test_a_row_without_an_id_is_skipped_not_printed_at(self):
        """A malformed mesh row must not become a `None` handed to a `str`
        parameter - and must not consume one of the writes either."""
        rows: List[Dict[str, Any]] = [{"id": None, "name": "Ghost.rbxl"}]
        rows += _rows(("a", "One.rbxl"), ("real", "Two.rbxl"))

        with self._patched(identity={"place_path": "Nowhere.rbxl"}, matches={"real": 11}):
            got = await inst._identify_launched(
                _Studio(rows), 11, "Nowhere.rbxl", allow_console_write=True
            )

        self.assertEqual(got["studio_id"], "real")
        self.assertNotIn(None, self.asked)
        self.assertEqual(got["console_writes"], 2)


class TheMeshDiffIsTheFirstRoute(unittest.IsolatedAsyncioTestCase):
    """The before/after diff decides, and it needs neither a log nor a print.

    This is the route that makes the token spray unnecessary in the normal case.
    The case it exists for is the one the name route provably cannot handle: two
    Studios open on the *same* place file, so both mesh names are identical and
    the name is not evidence of anything.
    """

    def setUp(self):
        self.asked: List[str] = []

    async def _no_token(self, client, studio_id, candidates):
        self.asked.append(studio_id)
        return {"resolved": False, "error": "should not be reached"}

    async def _run(self, rows, before_ids, identity=None, matching=(), **kw):
        """`matching` is the set of mesh names the fake log route accepts.

        Defaults to matching nothing, which is what forces the diff route to be
        the thing under test. An earlier version of this helper stubbed
        ``name_matches_identity`` to always return ``False`` and then asserted the
        log route answered - a test that could only fail, and for the wrong
        reason.
        """
        accepted = set(matching)

        async def token(client, studio_id, candidates):
            return await self._no_token(client, studio_id, candidates)

        with mock.patch.multiple(
            inst,
            logid=mock.Mock(
                live_identities=lambda pids, started=None: (
                    {pids[0]: identity} if identity else {}
                ),
                name_matches_identity=lambda name, ident: bool(name) and name in accepted,
            ),
            _resolve_by_console_token=token,
        ):
            return await inst._identify_launched(
                _Studio(rows), 777, "Mine.rbxl", before_ids=before_ids, **kw
            )

    async def test_the_new_id_is_found_with_no_log_and_no_print(self):
        rows = _rows(("old", "Mine.rbxl"), ("new", "Mine.rbxl"))
        got = await self._run(rows, {"old"})
        self.assertEqual(got["studio_id"], "new")
        self.assertEqual(got["console_writes"], 0)
        self.assertEqual(self.asked, [])

    async def test_it_decides_the_case_the_name_route_cannot(self):
        """Two Studios, identical place names, and no readable log.

        The name route returns two matches and the old code sprayed a token into
        both. The diff separates them with no write at all.
        """
        rows = _rows(("a", "Mine.rbxl"), ("b", "Mine.rbxl"))
        got = await self._run(rows, {"a"}, identity=None)
        self.assertEqual(got["studio_id"], "b")
        self.assertEqual(self.asked, [], "a print reached a Studio it need not have")

    async def test_two_arriving_is_ambiguous_not_a_pick(self):
        rows = _rows(("a", "One.rbxl"), ("b", "Two.rbxl"))
        got = await self._run(rows, set())
        self.assertIsNone(got["studio_id"])
        self.assertIn("more than one", got["how"])
        self.assertIn("cannot be singled out", got["why"])
        self.assertEqual(got["console_writes"], 0)
        self.assertEqual(self.asked, [], "ambiguity must not trigger a print either")

    async def test_no_snapshot_falls_through_to_the_log_route(self):
        """``before_ids=None`` means the mesh could not be read before the spawn.

        It must NOT be treated as an empty set: that would make every attached
        Studio look newly-arrived, and the first row would be returned - which is
        picking, the thing this function refuses to do.
        """
        rows = _rows(("a", "One.rbxl"), ("b", "Two.rbxl"))
        got = await self._run(
            rows, None, identity={"place_path": "Two.rbxl"}, matching={"Two.rbxl"}
        )
        self.assertEqual(got["studio_id"], "b")
        self.assertIn("own log command line", got["how"])

    async def test_the_spray_is_off_unless_authorised(self):
        """The measured 2026-10-01 defect, pinned as a default.

        Launching one Studio wrote join tokens into Studios the caller never
        named, including one the user had not put in scope. Two Studios,
        identical names, no snapshot, no readable log: with no authorisation,
        nobody is asked.
        """
        rows = _rows(("a", "Mine.rbxl"), ("b", "Mine.rbxl"))
        got = await self._run(rows, None, identity=None)
        self.assertIsNone(got["studio_id"])
        self.assertEqual(got["console_writes"], 0)
        self.assertEqual(self.asked, [])
        self.assertIn("no console write was authorised", got["how"])
        self.assertIn("list_roblox_studios", got["why"])

    async def test_an_unidentified_launch_says_why_and_what_not_to_do(self):
        """`why` must be populated: this is the field that stops the caller from
        reading an unresolved answer as a failed launch and retrying it."""
        rows = _rows(("a", "Mine.rbxl"), ("b", "Mine.rbxl"))
        got = await self._run(rows, None, identity=None)
        self.assertTrue(got.get("why"), "an unresolved identification needs a reason")


class TheChainOrderIsTheContract(unittest.IsolatedAsyncioTestCase):
    """`studio_id -> pid` must try the cheap routes before the console.

    Two cheap routes exist and both cost nothing: the process's own log, and the
    `-parentPid` edge for a play-test member whose mesh name is `null`. The
    console token is third. This pins that order, because the value of the
    `allow_console_write` flag is entirely in what it is allowed to precede.

    Patches individual functions on the **real** `logid` module rather than
    swapping the module for a `Mock`. Swapping it makes every attribute return a
    `Mock`, and `resolve_pid_for_studio` reaches code that then does
    `"/" in <Mock>` - a failure that looks like a bug in the code under test and
    is actually a fault in the harness.
    """

    #: PIDs the fake machine is running, as `list_studio_processes` would report.
    PROCESSES: List[Dict[str, Any]] = [
        {"pid": 10, "created": "2026-09-30T10:00:00Z"},
        {"pid": 11, "created": "2026-09-30T10:00:05Z"},
    ]

    def _patched(self, *, identities, match, ambiguous="two processes match"):
        from roblox_studio_mcp.extended import logid as real_logid

        return mock.patch.multiple(
            inst,
            list_studio_processes=mock.Mock(
                return_value=[dict(p) for p in self.PROCESSES]
            ),
            _resolve_by_console_token=mock.AsyncMock(
                side_effect=AssertionError("reached the console token")
            ),
        ), mock.patch.multiple(
            real_logid,
            live_identities=mock.Mock(return_value=identities),
            match_mesh_name=mock.Mock(return_value=match),
            ambiguous_reason=mock.Mock(return_value=ambiguous),
            parse_process_started=mock.Mock(
                side_effect=lambda raw: 1_000.0 if raw else None
            ),
        )

    async def test_a_single_log_match_never_reaches_the_console(self) -> None:
        identities = {
            10: {"place_path": "C:/x/Mine.rbxl", "task": "EditFile", "log": "a.log"},
            11: {"place_path": "C:/x/Other.rbxl", "task": "EditFile", "log": "b.log"},
        }
        inst_patch, logid_patch = self._patched(identities=identities, match=[10])
        with inst_patch, logid_patch:
            got = await inst.resolve_pid_for_studio(
                _Studio(_rows(("sid", "Mine.rbxl"))), "sid", True
            )

        self.assertTrue(got["resolved"])
        self.assertEqual(got["pid"], 10)
        self.assertNotIn("console token", str(got.get("how", "")))

    async def test_declining_the_console_reports_instead_of_printing(self) -> None:
        """`allow_console_write=False` is the caller's veto. It has to produce
        `needs_console_write` rather than a quiet no - and the patched token
        resolver raises if reached at all, so reaching it fails the test."""
        identities = {
            10: {"place_path": "C:/x/Mine.rbxl", "task": "EditFile", "log": "a.log"},
            11: {"place_path": "C:/x/Mine.rbxl", "task": "EditFile", "log": "b.log"},
        }
        inst_patch, logid_patch = self._patched(identities=identities, match=[10, 11])
        with inst_patch, logid_patch:
            got = await inst.resolve_pid_for_studio(
                _Studio(_rows(("sid", "Mine.rbxl"))), "sid", False
            )

        self.assertFalse(got["resolved"])
        self.assertTrue(got.get("needs_console_write"))
        self.assertEqual(sorted(got["candidates"]), [10, 11])


if __name__ == "__main__":
    unittest.main()