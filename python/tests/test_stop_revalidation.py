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

import json
import unittest
from unittest import mock

from roblox_studio_mcp.extended import instance as inst


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


def _patched(*, alive=True, identity="match"):
    """Stub liveness and the log-identity read behind the revalidation."""
    if identity == "match":
        current = {4242: {"log": _resolved()["log"], "task": "EditFile"}}
    elif identity == "moved":
        current = {4242: {"log": "0.741_20260930T120000Z_Studio_DEF_last.log",
                          "task": "EditFile"}}
    elif identity is None:
        current = {}
    else:
        current = identity
    return mock.patch.multiple(
        inst,
        _pid_alive=mock.Mock(return_value=alive),
        logid=mock.Mock(
            live_identities=mock.Mock(return_value=current),
        ),
    )


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
        with mock.patch.multiple(inst, _pid_alive=alive_mock,
                                 logid=log_mock), \
             mock.patch.object(inst.asyncio, "to_thread", side_effect=spy):
            got = await inst.revalidate_pid_for_stop(4242, _resolved())
        self.assertTrue(got["ok"], got)
        self.assertIn(alive_mock, seen, seen)
        self.assertIn(log_mock.live_identities, seen, seen)


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


if __name__ == "__main__":
    unittest.main()
