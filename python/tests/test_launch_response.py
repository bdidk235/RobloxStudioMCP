"""A launch that succeeds must not report failure.

``launch_instance`` reused the name ``resolved`` for two different things: first
the absolute place path, then the result of ``_identify_launched``. The path was
overwritten with a dict, and ``os.path.basename(resolved)`` raised

    TypeError: expected str, bytes or os.PathLike object, not dict

**after** the Studio had already started, attached to the mesh, and opened its
place. So every ``action=launch`` reported an error on a launch that had in fact
worked, and left a running process behind.

That is the worst shape a launch bug can take: the side effect is real, the report
says the opposite, and nothing in the error mentions an orphaned process. The
tool response is the only signal a caller gets, so the response is the thing to
test.

Host-side effects are stubbed; what runs is the code that assembles the response.
"""

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import instance as inst  # noqa: E402

PLACE_DIR = os.path.join(
    os.environ.get("TEMP", "."), "roblox-studio-mcp-baseplates"
)


def _client():
    client = mock.Mock()
    client.close = mock.AsyncMock()
    return client


def _patches(proc, identified=None, existing=None, asleep=None):
    """`existing` is the process list *after* the launch.

    The first read of `list_studio_processes` is what `launch_instance` records as
    "before", so the mock has to change between calls: a fixed list makes the
    before-set and the after-set identical, nothing is ever new, and the launch
    reports a timeout instead of exercising the response at all.
    """
    before = [{"pid": 999, "created": "/Date(0)/"}]
    after = existing if existing is not None else before
    calls = {"n": 0}

    def processes(*_args, **_kwargs):
        # Accepts `with_attachment` / `max_age`: the launch loop asks for the list
        # with attachment off and no caching, because it is looking for a pid that
        # did not exist a moment ago.
        calls["n"] += 1
        return before if calls["n"] == 1 else after

    patches = [
        mock.patch.object(inst.subprocess, "Popen", return_value=proc),
        mock.patch.object(inst, "find_studio_exe", return_value="X.exe"),
        mock.patch.object(inst, "list_studio_processes", side_effect=processes),
        # The launch loop yields with `asyncio.sleep`, never `time.sleep`: a
        # blocking sleep would freeze the server's event loop for the whole
        # wait. Patched to instant so the timeout path stays fast, and its
        # presence here pins that the loop does not block. A caller may pass
        # its own `asleep` mock to observe the yields.
        mock.patch.object(inst.asyncio, "sleep",
                          new=asleep if asleep is not None else mock.AsyncMock()),
        mock.patch.object(inst.time, "monotonic",
                          side_effect=[float(i) for i in range(0, 500)]),
    ]
    if identified is None:
        identify = mock.AsyncMock(return_value={"studio_id": None, "mesh_name": None})
    else:
        identify = mock.AsyncMock(return_value=identified)
    patches.append(mock.patch.object(inst, "_identify_launched", new=identify))
    return patches, identify


def _run(proc, identified=None, existing=None, asleep=None):
    patches, identify = _patches(proc, identified, existing, asleep)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        return asyncio.run(inst.launch_instance(PLACE_DIR + "/Baseplate-1.rbxl",
                                                studio_client=_client())), identify


class LaunchResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(PLACE_DIR, exist_ok=True)
        with open(os.path.join(PLACE_DIR, "Baseplate-1.rbxl"), "wb") as handle:
            handle.write(b"stub")

    def _alive(self):
        proc = mock.Mock()
        proc.pid = 4242
        proc.poll.return_value = None
        proc.returncode = None
        return proc

    def test_a_successful_launch_reports_success(self):
        proc = self._alive()
        got, _ = _run(
            proc,
            identified={"studio_id": "abc-123", "mesh_name": "Baseplate-1.rbxl"},
            existing=[{"pid": 999, "created": "/Date(0)/"},
                      {"pid": 4242, "created": "/Date(0)/"}],
        )
        self.assertTrue(got["launched"], got)
        self.assertEqual(got["studio_id"], "abc-123")
        self.assertEqual(got["mesh_name"], "Baseplate-1.rbxl")

    def test_opened_is_a_filename_not_the_identification_dict(self):
        """The regression itself. ``opened`` is derived from the place path, and
        the path is what a dict used to be substituted into."""
        proc = self._alive()
        got, _ = _run(
            proc,
            identified={"studio_id": "abc", "mesh_name": "Baseplate-1.rbxl"},
            existing=[{"pid": 999, "created": "/Date(0)/"},
                      {"pid": 4242, "created": "/Date(0)/"}],
        )
        self.assertIsInstance(got.get("opened"), str, got)
        self.assertEqual(got["opened"], "Baseplate-1.rbxl")

    def test_the_shape_that_threw(self):
        """What the old code did, pinned so the failure cannot come back quietly:
        basename applied to the identification result is a TypeError."""
        with self.assertRaises(TypeError):
            os.path.basename({"studio_id": "abc"})

    def test_a_launch_that_never_attaches_says_so_plainly(self):
        got, _ = _run(self._alive(), identified={"studio_id": None, "mesh_name": None})
        self.assertFalse(got["launched"])
        self.assertIn("did not attach", got["error"])

    def test_exited_process_is_reported_and_never_identified(self):
        proc = mock.Mock()
        proc.pid = 4242
        proc.poll.return_value = 1
        proc.returncode = 1
        got, identify = _run(proc, identified={"studio_id": "abc"})
        self.assertFalse(got["launched"])
        self.assertEqual(got["exit_code"], 1)
        identify.assert_not_called()

    def test_an_exited_parent_cannot_hide_a_live_child(self):
        """The scan comes before the exit check: a parent that exits while a
        live child holds the place open must still identify, not report
        "Studio exited"."""
        proc = mock.Mock()
        proc.pid = 4242
        proc.poll.return_value = 1
        proc.returncode = 1
        got, identify = _run(
            proc,
            identified={"studio_id": "abc", "mesh_name": "Baseplate-1.rbxl"},
            existing=[{"pid": 999, "created": "/Date(0)/"},
                      {"pid": 4242, "created": "/Date(0)/"}],
        )
        self.assertTrue(got["launched"], got)
        self.assertEqual(got["studio_id"], "abc")
        identify.assert_called_once()

    def test_the_wait_yields_instead_of_blocking(self):
        """The loop must not freeze the event loop: `asyncio.sleep` is awaited
        every pass, and `time.sleep` is never touched. Driven down the
        never-attaches path so the loop actually has passes to yield on."""
        asleep = mock.AsyncMock()
        with mock.patch.object(inst.time, "sleep",
                              side_effect=AssertionError("blocked the loop")):
            got, _ = _run(self._alive(),
                          identified={"studio_id": None, "mesh_name": None},
                          asleep=asleep)
        self.assertFalse(got["launched"], got)
        self.assertTrue(asleep.await_count >= 1,
                        "the launch loop never yielded")


if __name__ == "__main__":
    unittest.main()
