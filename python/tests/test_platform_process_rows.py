"""The Windows process list, read natively, and the fallback when it cannot.

``platform.process_rows`` used to spawn PowerShell for every answer - about a
second, measured - and the process list is read on every launch poll, every
stop resolution and every survey. The native read answers in milliseconds
without spawning anything.

Two things are pinned here, and the second is the one that matters: the
native read must not produce a *wrong* answer when it cannot read a process.
A Studio whose command line cannot be read would arrive with no role and no
place, which is a wrong answer rather than a missing one, so the read
declines and the query answers instead.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from roblox_studio_mcp.extended import platform

IS_WINDOWS = os.name == "nt"


class TheNativeRead(unittest.TestCase):
    """Shape and content, on the platform that has one."""

    @unittest.skipUnless(IS_WINDOWS, "the native read is Windows-only")
    def test_it_returns_the_shape_the_query_returned(self):
        rows = platform._process_rows_native()
        if rows is None:
            self.skipTest("the native read declined on this machine")
        for row in rows:
            self.assertIn("pid", row)
            self.assertIn("created", row)
            self.assertIn("command_line", row)
            self.assertIsInstance(row["pid"], int)
            self.assertIsInstance(row["created"], str)
            self.assertIsInstance(row["command_line"], str)

    @unittest.skipUnless(IS_WINDOWS, "the native read is Windows-only")
    def test_the_creation_time_is_the_shape_the_identity_chain_parses(self):
        # `logid.parse_process_started` reads the /Date(ms)/ form, and the
        # identity resolution matches a log's start stamp against it. A
        # native path that produced a different shape would silently stop
        # the chain from resolving.
        from roblox_studio_mcp.extended import logid

        rows = platform._process_rows_native()
        if rows is None:
            self.skipTest("the native read declined on this machine")
        for row in rows:
            self.assertIsNotNone(logid.parse_process_started(row["created"]))

    @unittest.skipUnless(IS_WINDOWS, "the native read is Windows-only")
    def test_it_finds_this_process(self):
        # The read is about the machine's Studio processes; with none running
        # there is nothing to assert beyond the empty answer.
        rows = platform._process_rows_native()
        if rows is None:
            self.skipTest("the native read declined on this machine")
        self.assertIsInstance(rows, list)


@unittest.skipUnless(IS_WINDOWS, "the Windows branch is what has a native read")
class TheFallback(unittest.TestCase):
    """An incomplete native read falls back to the query, never to a guess."""

    def _rows(self, native):
        with mock.patch.object(platform, "_process_rows_native", return_value=native):
            with mock.patch.object(
                platform, "_powershell_json",
                return_value=[{"ProcessId": 7, "CreationDate": "/Date(1000)/",
                               "CommandLine": "RobloxStudioBeta.exe --task EditFile"}],
            ) as query:
                return platform.process_rows(), query

    def test_an_incomplete_native_read_falls_back_to_the_query(self):
        rows, query = self._rows(None)
        self.assertTrue(query.called, "the query must answer when the native read cannot")
        self.assertEqual(rows[0]["pid"], 7)

    def test_a_complete_native_read_never_spawns_anything(self):
        native = [{"pid": 11, "created": "/Date(2000)/", "command_line": "x"}]
        rows, query = self._rows(native)
        self.assertFalse(query.called, "the native read answered, so nothing should spawn")
        self.assertEqual(rows, native)

    def test_the_native_rows_are_returned_unchanged(self):
        native = [{"pid": 11, "created": "/Date(2000)/", "command_line": "x"}]
        rows, _ = self._rows(native)
        self.assertIs(rows[0], native[0])


if __name__ == "__main__":
    unittest.main()
