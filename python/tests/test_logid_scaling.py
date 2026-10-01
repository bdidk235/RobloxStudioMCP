"""The prefix read must not lose an identity that a full read would find.

``read_identity`` reads 64 KB and only escalates to the whole file when that
prefix has no PID. That fallback is the whole safety argument for the prefix, so
it is tested against a log whose identity lines sit *past* the prefix, using a
small prefix so the test does not have to write 64 KB of padding.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import logid  # noqa: E402

BANNER = (
    r"C:\Roblox\Versions\version-x\RobloxStudioBeta.exe --task EditFile "
    r"--localPlaceFile C:\tmp\Baseplate-1.rbxl"
)
PID_LINE = (
    "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] "
    "Constructing UIThreadNotifier for process '4242' with id 'https://www.roblox.com-Studio'"
)
FILLER = "2026-09-30T09:16:55.000Z,2.0,1a78,6,Info [FLog::Somewhere] padding line\n"


class PrefixRead(unittest.TestCase):
    def setUp(self):
        self._prefix = logid.PREFIX_BYTES
        self._dir = tempfile.mkdtemp(prefix="logid-prefix-")

    def tearDown(self):
        logid.PREFIX_BYTES = self._prefix
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)

    def _write(self, name, text):
        path = os.path.join(self._dir, name)
        with open(path, "w", encoding="utf-8", errors="ignore") as handle:
            handle.write(text)
        return path

    def test_reads_identity_when_it_is_inside_the_prefix(self):
        logid.PREFIX_BYTES = 8192
        path = self._write("near.log", BANNER + "\n" + PID_LINE)
        got = logid.read_identity(path)
        self.assertEqual(got["pid"], 4242)
        self.assertTrue(got["place_path"].endswith("Baseplate-1.rbxl"))
        self.assertEqual(got["task"], "EditFile")

    def test_falls_back_when_the_pid_is_past_the_prefix(self):
        """The prefix holds the banner but not the PID. A resolver that trusted the
        prefix would report this live process as having no identity at all, and it
        would silently drop out of the join."""
        logid.PREFIX_BYTES = 256
        text = BANNER + "\n" + FILLER * 40 + PID_LINE
        path = self._write("far.log", text)
        got = logid.read_identity(path)
        self.assertEqual(got["pid"], 4242)

    def test_reports_no_pid_rather_than_guessing_when_absent_everywhere(self):
        logid.PREFIX_BYTES = 256
        path = self._write("none.log", BANNER + "\n" + FILLER * 40)
        self.assertIsNone(logid.read_identity(path)["pid"])

    def test_missing_file_is_none_not_an_exception(self):
        """A log being rotated or locked is normal, and one unreadable file must
        not fail the whole sweep."""
        self.assertIsNone(logid.read_identity(os.path.join(self._dir, "absent.log")))

    def test_prefix_default_is_measured_not_guessed(self):
        """All four identity lines fit in 3,809 bytes across 43 measured logs, so
        the 64 KB default is a ~17x margin. If someone lowers it to near 4 KB the
        margin disappears silently, so pin it."""
        self.assertGreaterEqual(logid.PREFIX_BYTES, 8192)


if __name__ == "__main__":
    unittest.main()
