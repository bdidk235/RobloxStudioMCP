"""The token fallback must name its target.

``_resolve_by_console_token`` is the last resort for the cases the log join cannot
decide - several URI launches of one place, and a playtest server or client whose
mesh name is ``null``. Both of those only arise when more than one Studio is
connected.

It used to call ``execute_luau`` without a ``studio_id`` and let the client infer
one, and inference refuses to guess with several Studios attached. So the
last-resort path failed in exactly the situation it was written for. Measured live
against two Studios both named ``Place1``::

    could not print a join token: 2 Roblox Studio instances are connected,
    so no studio_id can be inferred

These tests pin that the target is passed, and that a failure to print is still
reported as a failure rather than swallowed.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import instance as inst  # noqa: E402

SID = "12c0a3af-2854-4c8a-9c3f-000000000001"
OTHER = "12c0a3af-2854-4c8a-9c3f-000000000002"
# A candidate pool, because the join now refuses an empty one before it
# prints (audit A4): an empty pool used to mean "any pid is fair game",
# which let a token printed into the named Studio name an unrelated pid.
# These tests are about the print itself, so they reach it the way a real
# caller does - with the candidate set the studio_id resolved to.
POOL = [{"pid": 4242}]


class TokenPrint(unittest.TestCase):
    def test_names_the_studio_it_was_asked_about(self):
        """The regression: inference cannot choose when several Studios are
        connected, and several Studios is the only reason this path runs."""
        client = mock.Mock()
        client.execute_luau = mock.AsyncMock(side_effect=RuntimeError("stop here"))

        import asyncio

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/nonexistent"}):
            asyncio.run(inst._resolve_by_console_token(client, SID, POOL))

        client.execute_luau.assert_awaited_once()
        kwargs = client.execute_luau.await_args.kwargs
        self.assertEqual(kwargs.get("studio_id"), SID,
                         "the fallback must name its target explicitly")

    def test_a_print_failure_is_reported_not_swallowed(self):
        """If the token cannot be printed there is no join, and saying so beats
        returning a confident wrong answer."""
        client = mock.Mock()
        client.execute_luau = mock.AsyncMock(side_effect=RuntimeError("boom"))

        import asyncio

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/nonexistent"}):
            got = asyncio.run(inst._resolve_by_console_token(client, SID, POOL))

        self.assertFalse(got["resolved"])
        self.assertIn("could not print a join token", got["error"])
        self.assertIn("boom", got["error"])

    def test_the_printed_line_is_just_the_token(self):
        """A token print goes to the user's console, so it must be short and
        obviously ours - and must not be the vector for anything else."""
        client = mock.Mock()
        client.execute_luau = mock.AsyncMock(side_effect=RuntimeError("stop"))

        import asyncio

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/nonexistent"}):
            asyncio.run(inst._resolve_by_console_token(client, SID, POOL))

        code = client.execute_luau.await_args.args[0]
        self.assertTrue(code.startswith('print("RBXPID'))
        self.assertEqual(code.count("print("), 1)
        self.assertLess(len(code), 40)


if __name__ == "__main__":
    unittest.main()
