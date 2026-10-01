"""Tests for progress-aware request deadlines.

The real Studio server is not the right thing to test this against: whether it
emits progress at all is unknown, and a test that depends on that would be
testing the server, not the client. A fake stdio server lets both branches be
exercised deterministically:

* progress notifications extend the deadline, so a slow call survives
* silence still times out, so the deadline is not simply removed
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
        client = await self._client(delay=2.0, tick=0.1, progress=False, timeout=0.6)
        with self.assertRaises(Exception) as caught:
            await client.call_tool("slow", {})
        self.assertIn("Timed out", str(caught.exception))

    async def test_progress_extends_the_deadline(self):
        # 2.5s of work against a 0.6s timeout. Each progress tick pushes the
        # deadline out, so this must complete instead of timing out.
        client = await self._client(delay=2.5, tick=0.15, progress=True, timeout=0.6)
        result = await client.call_tool("slow", {})
        self.assertIn("done", str(result))

    async def test_progress_is_reported_to_the_hook(self):
        client = await self._client(delay=1.0, tick=0.1, progress=True, timeout=1.0)
        seen = []
        client.on_progress = lambda token, params: seen.append(token)
        await client.call_tool("slow", {})
        self.assertTrue(seen, "on_progress was never called")
        self.assertTrue(all(isinstance(t, int) for t in seen))

    async def test_a_raising_hook_does_not_break_the_call(self):
        client = await self._client(delay=0.8, tick=0.1, progress=True, timeout=0.6)

        def boom(token, params):
            raise RuntimeError("hook exploded")

        client.on_progress = boom
        result = await client.call_tool("slow", {})
        self.assertIn("done", str(result))

    async def test_deadline_state_is_cleaned_up(self):
        client = await self._client(delay=0.3, tick=0.1, progress=True, timeout=1.0)
        await client.call_tool("slow", {})
        self.assertEqual(client._deadlines, {}, "deadline state leaked past the call")

    async def test_deadline_state_is_cleaned_up_after_a_timeout(self):
        client = await self._client(delay=2.0, tick=0.1, progress=False, timeout=0.4)
        with self.assertRaises(Exception):
            await client.call_tool("slow", {})
        self.assertEqual(client._deadlines, {})


if __name__ == "__main__":
    unittest.main()
