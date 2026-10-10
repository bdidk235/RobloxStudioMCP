"""The stop path must not kill on stale evidence.

`resolve_pid_for_studio` answers which PID backs a `studio_id`, but the answer
describes a moment that has passed by the time `terminate_process` runs. Since
then the process may have exited - in which case the PID may already belong to
something else - or its log identity may have moved. Killing on the stale
answer hits the wrong process, which is the exact failure the resolver chain
exists to prevent.

So `action=stop` revalidates the PID immediately before terminating: liveness
first, then that the live log identity still matches what the resolve
reported. A refusal reports instead of killing. Nothing here touches
`terminate_process` itself, and no tool input changes.
"""

from __future__ import annotations

import contextlib
import json
import os
import unittest
from unittest import mock

from roblox_studio_mcp.extended import instance as inst
from roblox_studio_mcp.extended import locks as locks_mod


def _resolved(**over):
    base = {
        "resolved": True,
        "pid": 4242,
        "how": "matched the mesh name against each process's own log command line",
        "matched_on": "Mine.rbxl",
        "log": "0.741_20260930T100000Z_Studio_ABC_last.log",
        "task": "EditFile",
        "from_live_process_list": True,
    }
    base.update(over)
    return base


def _patched(*, alive=True, identity="match", witness=True):
    """Stub liveness, the log-identity read, and the independent witnesses.

    ``witness`` selects the A5 witness state: ``True`` means the attachment set
    confirms the pid (the normal live case), ``False`` means every witness
    source is empty (the refusal), and a dict of ``{attached: [...], locks:
    {...}}`` sets them explicitly.
    """
    if identity == "match":
        current = {4242: {"log": _resolved()["log"], "task": "EditFile"}}
    elif identity == "moved":
        current = {4242: {"log": "0.741_20260930T120000Z_Studio_DEF_last.log",
                          "task": "EditFile"}}
    elif identity is None:
        current = {}
    else:
        current = identity
    if witness is True:
        attached, locks_now = [4242], {}
    elif witness is False:
        attached, locks_now = [], {}
    else:
        attached = witness.get("attached", [])
        locks_now = {pid: name for pid, name in witness.get("locks", {}).items()}
    return _witness_ctx(
        alive=alive, current=current, attached=attached, locks_now=locks_now
    )


def _witness_ctx(*, alive, current, attached, locks_now):
    """Everything the revalidation touches, in one exit stack.

    ``locks`` is patched in its own module rather than as an ``instance``
    attribute: ``_independent_witness`` imports it locally, so
    ``mock.patch.multiple(inst, locks=...)`` raises - the module never had that
    name. The witness sources are the whole point of A5; see
    ``test_no_witness_means_no_kill``.
    """
    import contextlib

    stack = contextlib.ExitStack()
    for patcher in (
        mock.patch.object(inst, "_pid_alive", return_value=alive),
        mock.patch.object(inst.logid, "live_identities", return_value=current),
        mock.patch.object(inst.platform, "attached_pids", return_value=attached),
        mock.patch.object(locks_mod, "live_locks", return_value=locks_now),
    ):
        stack.enter_context(patcher)
    return stack


class RevalidatePidForStop(unittest.IsolatedAsyncioTestCase):
    async def test_a_live_unchanged_pid_passes(self):
        with _patched():
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertTrue(got["ok"], got)

    async def test_an_exited_pid_is_refused_not_killed(self):
        """The PID may already belong to something else; killing it would hit
        whatever reused the number."""
        with _patched(alive=False):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertFalse(got["ok"], got)
        self.assertIn("reused", got["error"])

    async def test_an_unreadable_identity_is_refused(self):
        with _patched(identity=None):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertFalse(got["ok"], got)
        self.assertIn("error", got)

    async def test_a_moved_identity_is_refused(self):
        """Same number, different log: something else now holds the PID."""
        with _patched(identity="moved"):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertFalse(got["ok"], got)
        self.assertIn("no longer matches", got["error"])

    async def test_missing_fields_are_lenient_not_fatal(self):
        """Answers that carry no log/task (older callers, narrower paths) can
        still be liveness-checked; absent evidence is not mismatched evidence."""
        with _patched():
            got = await inst.revalidate_pid_for_stop(
                4242, {"resolved": True, "pid": 4242})
        self.assertTrue(got["ok"], got)

    async def test_the_identity_read_leaves_the_event_loop(self):
        """Both blocking reads go through `to_thread`: this runs on the
        server's loop between resolve and kill."""
        seen = []
        alive_mock = mock.Mock(return_value=True)

        real_to_thread = inst.asyncio.to_thread

        async def spy(func, /, *args, **kwargs):
            seen.append(func)
            return await real_to_thread(func, *args, **kwargs)

        log_mock = mock.Mock(
            live_identities=mock.Mock(
                return_value={4242: {"log": _resolved()["log"],
                                    "task": "EditFile"}}))
        # A witness, because the revalidation now requires one: the log read
        # and the witness read must both leave the loop, not just the log.
        with mock.patch.multiple(inst, _pid_alive=alive_mock,
                                 logid=log_mock), \
             mock.patch.object(
                 inst.platform, "attached_pids",
                 mock.Mock(return_value=[4242])), \
             mock.patch.object(
                 locks_mod, "live_locks", mock.Mock(return_value={})), \
             mock.patch.object(inst.asyncio, "to_thread", side_effect=spy):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertTrue(got["ok"], got)
        self.assertIn(alive_mock, seen, seen)
        self.assertIn(log_mock.live_identities, seen, seen)


    async def test_no_witness_means_no_kill(self):
        """The A5 rule: a log alone is not evidence, because the log is writable.

        The resolve *and* the revalidation both read Studio's own logs, so a
        planted log survived both and reached the kill. With every witness
        source empty the answer is a refusal, however good the log looks.
        """
        with _patched(witness=False):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertFalse(got["ok"], got)
        self.assertIn("independent witness", got["error"])

    async def test_a_contradicting_witness_is_refused(self):
        """Witnesses that exist and name other pids are a mismatch, not silence.

        The lock file is the source Studio itself writes, so a pid the lock
        does not name while the log does is the planted-log case exactly.
        """
        with _patched(witness={"attached": [777, 888], "locks": {777: "Place1"}}):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertFalse(got["ok"], got)
        self.assertIn("independent witness", got["error"])
        self.assertIn("777", got["error"])
        self.assertIn("888", got["error"])

    async def test_a_lock_alone_confirms_without_the_attachment_set(self):
        """Either witness is enough - the lock and the socket table are
        independent, and requiring both would strand a machine whose Studio
        has not opened a place file."""
        with _patched(witness={"attached": [], "locks": {4242: "Place1"}}):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["witnessed_by"], ["the AutoSaves lock file"])

    async def test_the_attachment_set_alone_confirms_without_a_lock(self):
        with _patched(witness={"attached": [4242], "locks": {}}):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["witnessed_by"], ["the mesh attachment set"])

    async def test_an_unreadable_witness_source_is_not_a_contradiction(self):
        """A raise from either witness read is a missing witness, never a
        mismatch - and it still refuses, because no witness means no kill."""
        import contextlib

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(
            inst.platform, "attached_pids",
            mock.Mock(side_effect=RuntimeError("socket table unreadable"))))
        with stack, _patched(witness=False):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertFalse(got["ok"], got)


class ConsoleTokenFallbackRefusesAnEmptyPool(unittest.IsolatedAsyncioTestCase):
    """Audit A4: an empty candidate set refuses, it never widens to everything."""

    async def test_no_candidates_is_a_refusal_not_any_pid(self):
        client = mock.Mock()
        with mock.patch.object(
            inst.logid, "read_identity",
            return_value={"pid": 9999, "task": "EditFile"}), \
            mock.patch.object(inst.os, "listdir", return_value=[]):
            got = await inst._resolve_by_console_token(client, "sid-1", [])
        self.assertFalse(got["resolved"], got)
        self.assertIn("candidate", got["error"])
        # The load-bearing half: the Studio was never written to.
        client.execute_luau.assert_not_called()

    async def test_a_candidate_pool_still_resolves(self):
        """The refusal must not break the real path: a pool of one still joins.

        Uses a real temp log directory with a real token in it, because the
        token is generated inside the function from `uuid4` - a fake that
        cannot see it would have to fake the whole log read, and then the test
        would prove only that the mock returned what it was told.
        """
        import tempfile
        import uuid
        from unittest import mock as _m

        client = mock.AsyncMock()
        client.execute_luau = mock.AsyncMock(return_value=None)
        token = "RBXPIDAABBCCDDEE"
        with tempfile.TemporaryDirectory() as logs:
            log_name = "0.741_20260930T120000Z_Studio_9A2B_last.log"
            with open(os.path.join(logs, log_name), "w", encoding="utf-8") as fh:
                # A real log shape: the token the sweep looks for, plus the
                # ``Constructing UIThreadNotifier`` line `read_identity` pulls
                # the pid from.
                fh.write(token + "\n")
                fh.write("[FLog::Output] 1.23456 7dbc: Constructing "
                         "UIThreadNotifier for process '4242'\n")
            with mock.patch.object(inst.logid, "log_dir", return_value=logs), \
                 mock.patch.object(uuid, "uuid4",
                                   return_value=_m.Mock(hex="aabbccddeeff00112233")):
                got = await inst._resolve_by_console_token(
                    client, "sid-1", [{"pid": 4242}])
        self.assertTrue(got["resolved"], got)
        self.assertEqual(got["pid"], 4242)
        client.execute_luau.assert_awaited_once()


class StopDryRun(unittest.IsolatedAsyncioTestCase):
    """`dry_run` reports the stop target instead of terminating (audit A4).

    The point is inspection at zero risk: the pid that WOULD be stopped, what
    resolved it, and whether an independent witness would confirm it. Every
    outcome is a report, including the ones that would refuse a real stop -
    a dry run must never raise for finding the problem it was called to find.
    """

    async def _stop(self, arguments, *, rechecked=None, resolved=None):
        from roblox_studio_mcp import extended_server as srv

        seen = {}
        killed = []
        term = mock.Mock(side_effect=lambda pid: killed.append(pid) or
                         {"stopped": True, "pid": pid})

        async def resolver(client, studio_id, *, allow_console_write=None):
            seen["allow_console_write"] = allow_console_write
            return resolved if resolved is not None else _resolved()

        async def revalidate(pid, answer):
            seen["pid"] = pid
            return rechecked or {"ok": True, "witnessed_by": ["the mesh attachment set"]}

        with mock.patch.object(
            inst, "resolve_pid_for_studio", new=mock.AsyncMock(side_effect=resolver),
        ), mock.patch.object(
            inst, "revalidate_pid_for_stop", new=mock.AsyncMock(side_effect=revalidate),
        ), mock.patch.object(inst, "terminate_process", new=term):
            raw = await srv._call_manage_instance(
                mock.Mock(), dict({"action": "stop", "studio_id": "sid-1"},
                                  **arguments))
        return json.loads(raw["content"][0]["text"]), seen, killed

    async def test_a_dry_run_reports_and_never_kills(self):
        got, _seen, killed = await self._stop({"dry_run": True})
        self.assertTrue(got["dry_run"], got)
        self.assertFalse(got["stopped"], got)
        self.assertEqual(got["pid"], 4242)
        self.assertTrue(got["would_stop"], got)
        self.assertEqual(got["witnessed_by"], ["the mesh attachment set"])
        self.assertEqual(got["resolved_by"], "matched the mesh name against each "
                                            "process's own log command line")
        # The load-bearing half: the kill mock was never reached, not merely
        # overridden.
        self.assertEqual(killed, [], killed)

    async def test_a_dry_run_reports_a_future_refusal_without_raising(self):
        """The value of the flag: it finds the refusal before the kill is tried."""
        got, _seen, killed = await self._stop(
            {"dry_run": True},
            rechecked={"ok": False, "code": "WITNESS_MISMATCH",
                       "error": "no independent witness confirms it"},
        )
        self.assertTrue(got["dry_run"], got)
        self.assertFalse(got["would_stop"], got)
        self.assertTrue(got["would_refuse"], got)
        self.assertIn("independent witness", got["error"])
        self.assertEqual(killed, [], killed)

    async def test_the_same_revalidation_refusal_raises_for_a_real_stop(self):
        """The pairing: without the flag, the very same finding is an error.

        A dry run reports it and a real stop refuses with it - one code, two
        surfaces, and the flag decides which. This pins that the difference is
        real rather than accidental.
        """
        from roblox_studio_mcp import extended_server as srv

        from roblox_studio_mcp.extended.errors import ToolError

        with mock.patch.object(
            inst, "resolve_pid_for_studio",
            new=mock.AsyncMock(return_value=_resolved()),
        ), mock.patch.object(
            inst, "revalidate_pid_for_stop",
            new=mock.AsyncMock(return_value={
                "ok": False, "code": "WITNESS_MISMATCH", "error": "no witness",
            }),
        ), mock.patch.object(inst, "terminate_process", new=mock.Mock()):
            with self.assertRaises(ToolError) as caught:
                await srv._call_manage_instance(
                    mock.Mock(), {"action": "stop", "studio_id": "sid-1"})
        self.assertEqual(caught.exception.code, "WITNESS_MISMATCH")
        self.assertEqual(caught.exception.data["studio_id"], "sid-1")

    async def test_a_dry_run_does_not_print_the_token(self):
        """The token is the one side effect `stop` is otherwise permitted.

        A dry run that printed would break the promise in its own description,
        so the console-write grant is withdrawn rather than narrowed.
        """
        _got, seen, _killed = await self._stop({"dry_run": True})
        self.assertFalse(seen["allow_console_write"], seen)

    async def test_a_real_stop_still_grants_the_token(self):
        """Without the flag the grant is as it was - backward compatible."""
        got, seen, killed = await self._stop({})
        self.assertTrue(seen["allow_console_write"], seen)
        self.assertTrue(got["stopped"], got)
        self.assertNotIn("dry_run", got)
        self.assertEqual(killed, [4242], killed)

    async def test_an_unresolvable_dry_run_still_reports_the_diagnosis(self):
        _got, _seen, killed = await self._stop(
            {"dry_run": True},
            resolved={"resolved": False, "error": "no connected Studio has id 'sid-1'"},
        )
        self.assertFalse(_got["resolved"], _got)
        self.assertTrue(_got["dry_run"], _got)
        self.assertFalse(_got["stopped"], _got)
        self.assertIn("no connected Studio", _got["error"])
        self.assertEqual(killed, [], killed)


class StopDispatchRevalidates(unittest.IsolatedAsyncioTestCase):
    """The `stop` action reports a revalidation refusal instead of killing."""

    async def _stop(self, rechecked):
        from roblox_studio_mcp import extended_server as srv

        with mock.patch.object(
            inst, "resolve_pid_for_studio",
            new=mock.AsyncMock(return_value=_resolved()),
        ), mock.patch.object(
            inst, "revalidate_pid_for_stop",
            new=mock.AsyncMock(return_value=rechecked),
        ), mock.patch.object(
            inst, "terminate_process",
            side_effect=AssertionError("killed without revalidating")
            if not rechecked.get("ok")
            else mock.Mock(return_value={"stopped": True, "pid": 4242}),
        ):
            return await srv._call_manage_instance(
                mock.Mock(),
                {"action": "stop", "studio_id": "sid-1"},
            )

    async def test_a_refusal_reports_and_never_kills(self):
        raw = await self._stop({"ok": False, "error": "pid exited; refusing"})
        got = json.loads(raw["content"][0]["text"])
        self.assertEqual(got["action"], "stop")
        self.assertFalse(got["stopped"], got)
        self.assertIn("refusing", got["error"])
        self.assertNotIn("ok", got)

    async def test_a_pass_kills_as_before(self):
        raw = await self._stop({"ok": True})
        got = json.loads(raw["content"][0]["text"])
        self.assertTrue(got["stopped"], got)
        self.assertEqual(got["pid"], 4242)

    async def test_the_console_write_authorisation_is_named_at_the_call_site(self):
        """``stop`` grants console-write, and says so in the call, not in a literal.

        The flag is an authorisation point: it permits the last-resort join to
        print a random token into the console of the Studio named by
        ``studio_id`` and read it back out of Studio's logs. A bare positional
        ``True`` grants that invisibly, which is what two independent reviewers
        flagged - and what a later tidy-up would quietly restore, since removing
        the keyword changes nothing observable.

        So the *keyword* is what is pinned, and the spy declares
        ``allow_console_write`` keyword-only: the real signature takes it
        positionally, and a call that passes it positionally does not reach the
        body at all. First version of this test took the argument
        positionally-or-keyword and **passed against the unfixed code** - a guard
        that cannot fail is not a guard. Behaviour is unchanged either way: the
        flag is already ``True`` today.
        """
        from roblox_studio_mcp import extended_server as srv

        seen = {}

        async def spy(client, studio_id, *, allow_console_write=None):
            seen["allow_console_write"] = allow_console_write
            return _resolved()

        with mock.patch.object(inst, "resolve_pid_for_studio", new=spy), \
             mock.patch.object(inst, "revalidate_pid_for_stop",
                               new=mock.AsyncMock(return_value={"ok": True})), \
             mock.patch.object(inst, "terminate_process",
                               mock.Mock(return_value={"stopped": True})):
            await srv._call_manage_instance(
                mock.Mock(), {"action": "stop", "studio_id": "sid-1"}
            )

        self.assertTrue(seen["allow_console_write"], seen)


if __name__ == "__main__":
    unittest.main()


class WitnessReadsTheCheapSourceFirst(unittest.IsolatedAsyncioTestCase):
    """The A5 witness ordering: the lock is read before the socket table.

    Not a micro-optimisation for its own sake. The socket query was 27 s through
    PowerShell and either witness alone confirms, so reading it first spent the
    expensive half and discarded it whenever the lock had already answered. It is
    ~89 ms natively now (`platform._mesh_holders_native`), which makes this
    ordering cheap in absolute terms - but it is still the difference between a
    file read and a syscall, and it is pinned so a later tidy-up cannot quietly
    restore the expensive order.
    """

    async def _run_with_witness(self, witness):
        """Run the revalidation, returning (answer, socket-read mock).

        The mock is the patched ``attached_pids`` itself rather than a spy, so it
        is captured inside the patch's lifetime - asserting on
        ``inst.platform.attached_pids`` after the ``with`` block reads the real
        function, which has no assert methods and proves nothing.
        """
        stack = contextlib.ExitStack()
        stack.enter_context(_patched(witness=witness))
        socket_read = mock.patch.object(
            inst.platform, "attached_pids",
            return_value=witness.get("attached", []) if isinstance(witness, dict) else [],
        )
        socket_read = stack.enter_context(socket_read)
        try:
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        finally:
            stack.close()
        return got, socket_read

    async def test_a_confirmed_lock_never_touches_the_socket_table(self):
        got, socket_read = await self._run_with_witness(
            {"attached": [], "locks": {4242: "Place1"}})
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["witnessed_by"], ["the AutoSaves lock file"])
        # The load-bearing assertion, and the one a reordering would break.
        socket_read.assert_not_called()

    async def test_the_socket_table_is_read_when_the_lock_is_silent(self):
        got, socket_read = await self._run_with_witness(
            {"attached": [4242], "locks": {}})
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["witnessed_by"], ["the mesh attachment set"])
        socket_read.assert_called_once()

    async def test_the_socket_read_asks_one_membership_question(self):
        """The intersection is scoped, so the process enumeration is skipped.

        The target is already known to be a Studio process - the resolver pulled
        it out of the live process list. Passing `[target]` as the scoping set
        means `attached_pids` computes `holders & {target}` and never enumerates
        processes to build a list nobody asked for.
        """
        _got, socket_read = await self._run_with_witness(
            {"attached": [4242], "locks": {}})
        socket_read.assert_called_once_with([4242])

    async def test_neither_witness_still_refuses(self):
        got, socket_read = await self._run_with_witness(False)
        self.assertFalse(got["ok"], got)
        self.assertIn("independent witness", got["error"])
        socket_read.assert_called_once()
