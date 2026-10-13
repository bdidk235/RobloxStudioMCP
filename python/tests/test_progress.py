"""Tests for progress-aware request deadlines.

The real Studio server is not the right thing to test this against: whether it
emits progress at all is unknown, and a test that depends on that would be
testing the server, not the client. A fake stdio server lets both branches be
exercised deterministically:

* progress notifications extend the deadline, so a slow call survives
* silence still times out, so the deadline is not simply removed

The delays are **ratios, not durations**, and only one arm needs a tight one.
`test_progress_extends_the_deadline` is the claim itself, so its numbers are
coupled: a tick must land inside the window it extends, and it gets 0.7s of
slack to do it. Every other arm is deliberately **decoupled** - generous
timeout, short work, assertion independent of when a notification lands -
because a hook-reaches-the-hook assertion says nothing about scheduling.

The history behind that split, so nobody re-shrinks it by eye: the file used
2.5/1.0/0.8s of real sleeping to test arithmetic. A 2026-10-13 pass scaled it
down to 0.05s ticks against 0.3s deadlines and `test_a_raising_hook_does_not_
break_the_call` failed 1 run in 12 under `-n 4` on a 9p-mounted tree, where a
delayed notification and a real regression are indistinguishable at that
margin. Inflating the numbers alone would have hidden that; decoupling is what
actually removed it.
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from roblox_studio_mcp.client import MCPClient

# A stdio server that answers tools/slow after `delay` seconds, optionally
# emitting progress notifications every `tick` seconds.
FAKE = r'''
import json, sys, time

DELAY = %(delay)s
TICK = %(tick)s
PROGRESS = %(progress)s

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid = msg.get("id")
    method = msg.get("method", "")
    if mid is None:
        continue
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": mid,
               "result": {"protocolVersion": "2024-11-05",
                          "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}}}
    elif method == "tools/call":
        token = (msg.get("params") or {}).get("_meta", {}).get("progressToken")
        start = time.time()
        if PROGRESS and token is not None:
            while time.time() - start < DELAY:
                time.sleep(TICK)
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": 1},
                }) + "\n")
                sys.stdout.flush()
        else:
            time.sleep(DELAY)
        out = {"jsonrpc": "2.0", "id": mid,
               "result": {"content": [{"type": "text", "text": "done"}]}}
    else:
        out = {"jsonrpc": "2.0", "id": mid, "result": {}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
'''


def write_fake(tmp: Path, delay: float, tick: float, progress: bool) -> Path:
    script = tmp / "fake_server.py"
    script.write_text(
        FAKE % {"delay": delay, "tick": tick, "progress": progress}, encoding="utf-8"
    )
    return script


class TestProgressDeadlines(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    async def _client(self, delay, tick, progress, timeout):
        script = write_fake(self.tmp, delay, tick, progress)
        client = MCPClient(sys.executable, [str(script)], timeout=timeout)
        await client.connect()
        self.addCleanup(lambda: asyncio.get_event_loop().run_until_complete(client.close()))
        return client

    async def test_silence_still_times_out(self):
        # The deadline must survive the change: no progress means timeout.
        client = await self._client(delay=0.6, tick=0.05, progress=False, timeout=0.3)
        with self.assertRaises(Exception) as caught:
            await client.call_tool("slow", {})
        self.assertIn("Timed out", str(caught.exception))

    async def test_progress_extends_the_deadline(self):
        # 1.6s of work against a 0.8s timeout, a tick every 0.1s. Each tick
        # pushes the deadline out, so this must complete instead of timing out.
        #
        # The one arm that *must* couple to the ceiling: its claim is the
        # extension itself, so a slow tick is a real failure here. Everything
        # below is deliberately decoupled from timing instead (see the module
        # docstring) - measured 2026-10-13, an arm whose completion depends on
        # a tick landing inside a 0.5s window failed 1 run in 12 under `-n 4`.
        client = await self._client(delay=1.6, tick=0.1, progress=True, timeout=0.8)
        result = await client.call_tool("slow", {})
        self.assertIn("done", str(result))

    async def test_progress_is_reported_to_the_hook(self):
        # Timing-independent: the claim is that a notification reaches the
        # hook, so the timeout is generous and only the work is short.
        client = await self._client(delay=0.2, tick=0.05, progress=True, timeout=5.0)
        seen = []
        client.on_progress = lambda token, params: seen.append(token)
        await client.call_tool("slow", {})
        self.assertTrue(seen, "on_progress was never called")
        self.assertTrue(all(isinstance(t, int) for t in seen))

    async def test_a_raising_hook_does_not_break_the_call(self):
        # Timing-independent for the same reason, and this is the arm the 2026
        # shrink made flaky: its claim is that a raising hook does not break
        # the call, which says nothing about *when* the notifications land.
        client = await self._client(delay=0.2, tick=0.05, progress=True, timeout=5.0)

        def boom(token, params):
            raise RuntimeError("hook exploded")

        client.on_progress = boom
        result = await client.call_tool("slow", {})
        self.assertIn("done", str(result))

    async def test_deadline_state_is_cleaned_up(self):
        # No progress needed: `_deadlines` gains an entry per request, so the
        # cleanup claim stands without a notification stream.
        client = await self._client(delay=0.15, tick=0.05, progress=False, timeout=5.0)
        await client.call_tool("slow", {})
        self.assertEqual(client._deadlines, {}, "deadline state leaked past the call")

    async def test_deadline_state_is_cleaned_up_after_a_timeout(self):
        # Jitter-safe by construction: work takes 0.6s against a 0.3s deadline,
        # so a false pass would need the server to answer early.
        client = await self._client(delay=0.6, tick=0.05, progress=False, timeout=0.3)
        with self.assertRaises(Exception):
            await client.call_tool("slow", {})
        self.assertEqual(client._deadlines, {})


if __name__ == "__main__":
    unittest.main()
