"""Tests for the lock-file join. This module shipped with none.

``locks.py`` claims a ``studio_id`` -> PID join that needs no log read at all:

    studio_id -> mesh name -> <mesh name>.lock -> first field

Written but never wired into anything and never tested, while being described in
its own docstring as exact. The strings below are the real contents of two locks
on this machine:

    84633881964039_AutoRecovery_0.rbxl.lock   -> 18896 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-... |  |
    Template_95206881_AutoRecovery_3.rbxl.lock-> 12324 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-... |  |

The first named a process that had already exited - the stale-lock trap, which is
the one way this can return a confidently wrong PID.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import locks  # noqa: E402

#: Real contents, trailing empty fields and all.
LOCK_12324 = ("12324 | RobloxStudioBeta | DESKTOP-IH0RL4D | "
              "6cc6718a-7802-46dd-a529-5a5b57dacc35 |  | \n")
LOCK_18896 = ("18896 | RobloxStudioBeta | DESKTOP-IH0RL4D | "
              "6cc6718a-7802-46dd-a529-5a5b57dacc35 |  | \n")

#: The shape the docstring claims to tolerate: the document claimed by another
#: user puts something other than the pid in field 0.
LOCK_OTHER_USER_FIRST = ("DOMAIN\\someone | RobloxStudioBeta | WORKSTATION | "
                         "6cc6718a-7802-46dd-a529-5a5b57dacc35 | 77 | \n")


class ParseLock(unittest.TestCase):
    def test_reads_the_measured_line(self):
        got = locks.parse_lock(LOCK_12324)
        self.assertEqual(got["pid"], 12324)
        self.assertEqual(got["process"], "RobloxStudioBeta")
        self.assertEqual(got["machine"], "DESKTOP-IH0RL4D")
        self.assertEqual(got["session"], "6cc6718a-7802-46dd-a529-5a5b57dacc35")

    def test_trailing_empty_fields_do_not_shift_anything(self):
        """The real line ends in ``|  | ``, so a naive split produces empty trailing
        fields. A parser that indexed them would return empty strings for the
        fields it claims to read."""
        got = locks.parse_lock(LOCK_12324)
        self.assertTrue(got["process"])
        self.assertTrue(got["machine"])
        self.assertTrue(got["session"])

    def test_finds_the_pid_when_it_is_not_the_first_field(self):
        self.assertEqual(
            locks.parse_lock(LOCK_OTHER_USER_FIRST)["pid"], 77
        )

    def test_garbage_is_none_not_a_guess(self):
        for text in ("", "   \n", "no digits at all here\n", "\x00\x01"):
            self.assertIsNone(locks.parse_lock(text), repr(text))

    def test_crlf_is_handled(self):
        got = locks.parse_lock("12324 | RobloxStudioBeta | HOST | guid |  | \r\n")
        self.assertEqual(got["pid"], 12324)
        self.assertEqual(got["process"], "RobloxStudioBeta")

    def test_reads_the_measured_newline_shape(self):
        """Real locks have no pipe byte: one field per line. Before the fix
        this returned process/machine/session None, because the first line
        split on "|" yields a single field. The pipe fixtures above still
        pass - both shapes are accepted, and the measured one is now pinned."""
        text = ("12324\nRobloxStudioBeta\nDESKTOP-IH0RL4D\n"
                "6cc6718a-7802-46dd-a529-5a5b57dacc35\n\n")
        got = locks.parse_lock(text)
        self.assertEqual(got["pid"], 12324)
        self.assertEqual(got["process"], "RobloxStudioBeta")
        self.assertEqual(got["machine"], "DESKTOP-IH0RL4D")
        self.assertEqual(got["session"], "6cc6718a-7802-46dd-a529-5a5b57dacc35")

    def test_newline_shape_with_crlf(self):
        got = locks.parse_lock("12324\r\nRobloxStudioBeta\r\nHOST\r\nguid\r\n")
        self.assertEqual(got["pid"], 12324)
        self.assertEqual(got["session"], "guid")


class LockName(unittest.TestCase):
    def test_appends_lock_to_a_bare_name(self):
        self.assertEqual(
            locks.lock_name_for("Template_95206881_AutoRecovery_3.rbxl"),
            "Template_95206881_AutoRecovery_3.rbxl.lock",
        )

    def test_rejects_anything_with_a_path_separator(self):
        """The mesh reports a basename. A name carrying a separator must not be
        joined on, or a crafted place name could reach outside the lock
        directory."""
        for name in ("a/b.rbxl", "..\\..\\evil.rbxl", "C:\\x.rbxl", ""):
            self.assertEqual(locks.lock_name_for(name), "", name)

    def test_a_baseplate_name_maps_to_its_lock(self):
        self.assertEqual(
            locks.lock_name_for("Baseplate-555883251.rbxl"),
            "Baseplate-555883251.rbxl.lock",
        )


class PidFromLock(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="locks-test-")
        self._restore = locks.autosaves_dir
        locks.autosaves_dir = lambda: self._dir

    def tearDown(self):
        locks.autosaves_dir = self._restore
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)

    def _lock(self, name, text):
        with open(os.path.join(self._dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_resolves_a_live_owner(self):
        self._lock("Template_95206881_AutoRecovery_3.rbxl.lock", LOCK_12324)
        got = locks.pid_from_lock("Template_95206881_AutoRecovery_3.rbxl",
                                  live_pids={12324, 999})
        self.assertTrue(got["resolved"], got)
        self.assertEqual(got["pid"], 12324)
        self.assertEqual(got["session"], "6cc6718a-7802-46dd-a529-5a5b57dacc35")

    def test_a_stale_lock_is_never_returned_as_a_match(self):
        """The trap this exists to avoid. The lock outlives its process - measured,
        ``84633881964039_AutoRecovery_0.rbxl.lock`` was still naming pid 18896 long
        after it exited - and returning that would be a confidently wrong PID."""
        self._lock("84633881964039_AutoRecovery_0.rbxl.lock", LOCK_18896)
        got = locks.pid_from_lock("84633881964039_AutoRecovery_0.rbxl",
                                  live_pids={12324, 999})
        self.assertFalse(got["resolved"], got)
        self.assertTrue(got["stale"])
        self.assertEqual(got["pid"], 18896)
        self.assertIn("not running", got["error"])

    def test_without_a_live_set_a_stale_lock_still_resolves(self):
        """Documented behaviour, and a footgun: omitting ``live_pids`` is how a
        stale lock gets trusted. Pinned so the omission is visible, not so it is
        encouraged."""
        self._lock("84633881964039_AutoRecovery_0.rbxl.lock", LOCK_18896)
        got = locks.pid_from_lock("84633881964039_AutoRecovery_0.rbxl")
        self.assertTrue(got["resolved"])
        self.assertEqual(got["pid"], 18896)

    def test_missing_lock_says_the_document_holds_none(self):
        got = locks.pid_from_lock("Place1.rbxl", live_pids={1})
        self.assertFalse(got["resolved"])
        self.assertIn("holds no lock", got["error"])

    def test_a_path_shaped_name_is_refused_before_any_read(self):
        got = locks.pid_from_lock("..\\..\\evil.rbxl", live_pids={1})
        self.assertFalse(got["resolved"])
        self.assertIn("bare filename", got["error"])


class LiveLocks(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="locks-live-")
        self._restore = locks.autosaves_dir
        locks.autosaves_dir = lambda: self._dir

    def tearDown(self):
        locks.autosaves_dir = self._restore
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)

    def _lock(self, name, text):
        with open(os.path.join(self._dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_live_only_when_a_set_is_given(self):
        self._lock("a.rbxl.lock", LOCK_12324)
        self._lock("b.rbxl.lock", LOCK_18896)
        self.assertEqual(sorted(locks.live_locks({12324})), [12324])
        self.assertEqual(sorted(locks.live_locks()), [12324, 18896])

    def test_names_are_the_mesh_shape(self):
        """The reverse direction has to produce exactly what the mesh reports,
        because that string is what the join matches on."""
        self._lock("Template_95206881_AutoRecovery_3.rbxl.lock", LOCK_12324)
        self.assertEqual(
            locks.names_from_locks({12324}),
            ["Template_95206881_AutoRecovery_3.rbxl"],
        )

    def test_round_trip_name_to_pid(self):
        """The whole point of the module, end to end on one name."""
        self._lock("Template_95206881_AutoRecovery_3.rbxl.lock", LOCK_12324)
        for name in locks.names_from_locks({12324}):
            got = locks.pid_from_lock(name, live_pids={12324})
            self.assertTrue(got["resolved"], got)
            self.assertEqual(got["pid"], 12324)

    def test_a_missing_directory_is_empty_not_an_error(self):
        locks.autosaves_dir = lambda: os.path.join(self._dir, "gone")
        self.assertEqual(locks.live_locks({1}), {})
        self.assertEqual(locks.names_from_locks({1}), [])


if __name__ == "__main__":
    unittest.main()
