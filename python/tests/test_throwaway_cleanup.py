"""Temp place copies are owned and guarded.

``make_throwaway_place`` copies a baseplate to a ``roblox-studio-*.rbxl`` temp
file that Studio holds open while it runs, so nothing here can delete it at
exit: the exit is unobservable without a process handle, and on Windows the
remove fails while Studio holds the file. The policy is therefore explicit
rather than automatic - the caller removes its copy with
``cleanup_throwaway_place`` when done. The guard is the load-bearing part: a
cleanup call must never remove a file this project did not make.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import instance as inst  # noqa: E402


def _source_file(directory):
    source = os.path.join(directory, "Baseplate-source.rbxl")
    with open(source, "wb") as handle:
        handle.write(b"stub-place")
    return source


class MakeThrowawayPlace(unittest.TestCase):
    def test_copy_lands_in_the_temp_dir_with_the_guarded_name(self):
        with tempfile.TemporaryDirectory() as home:
            source = _source_file(home)
            with mock.patch.object(inst, "find_baseplate", return_value=source):
                made = inst.make_throwaway_place()
            try:
                self.assertIsInstance(made, str, made)
                self.assertEqual(
                    os.path.dirname(os.path.abspath(made)),
                    os.path.abspath(tempfile.gettempdir()),
                    made,
                )
                name = os.path.basename(made)
                self.assertTrue(name.startswith(inst.THROWAWAY_PREFIX), name)
                self.assertTrue(name.endswith(inst.THROWAWAY_SUFFIX), name)
                with open(made, "rb") as handle:
                    self.assertEqual(handle.read(), b"stub-place")
            finally:
                inst.cleanup_throwaway_place(made)
            self.assertFalse(os.path.exists(made))

    def test_missing_baseplate_still_raises_before_touching_temp(self):
        with mock.patch.object(inst, "find_baseplate", return_value=None):
            with self.assertRaises(FileNotFoundError):
                inst.make_throwaway_place()


class CleanupThrowawayPlace(unittest.TestCase):
    def test_removes_its_own_copy_and_reports_true(self):
        with tempfile.TemporaryDirectory() as home:
            source = _source_file(home)
            with mock.patch.object(inst, "find_baseplate", return_value=source):
                made = inst.make_throwaway_place()
            self.assertTrue(inst.cleanup_throwaway_place(made))
            self.assertFalse(os.path.exists(made))

    def test_second_remove_reports_false_without_raising(self):
        with tempfile.TemporaryDirectory() as home:
            source = _source_file(home)
            with mock.patch.object(inst, "find_baseplate", return_value=source):
                made = inst.make_throwaway_place()
            self.assertTrue(inst.cleanup_throwaway_place(made))
            self.assertFalse(inst.cleanup_throwaway_place(made))

    def test_refuses_a_foreign_name_in_the_temp_dir(self):
        foreign = os.path.join(
            tempfile.gettempdir(), "not-a-throwaway-place.rbxl")
        with open(foreign, "wb") as handle:
            handle.write(b"do not touch")
        try:
            self.assertFalse(inst.cleanup_throwaway_place(foreign))
            self.assertTrue(os.path.exists(foreign))
        finally:
            os.remove(foreign)

    def test_refuses_a_matching_name_outside_the_temp_dir(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            planted = os.path.join(
                elsewhere, inst.THROWAWAY_PREFIX + "keep" + inst.THROWAWAY_SUFFIX)
            with open(planted, "wb") as handle:
                handle.write(b"do not touch")
            self.assertFalse(inst.cleanup_throwaway_place(planted))
            self.assertTrue(os.path.exists(planted))


if __name__ == "__main__":
    unittest.main()
