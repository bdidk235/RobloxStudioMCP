"""Exercise the three `find_studio_exe` failure branches against a fake tree.

The message it used to emit was one sentence for all three cases: "An update may
be in progress; wait for it to finish." That is a cause the code cannot verify,
and it is wrong for two of the three situations - measured on this machine, where
"no Studio executable" is the *normal* state of a Player-only machine, and where a
renamed executable produced the same message while telling the agent to wait for
an update that was never happening.

Each branch needs a different recovery, so each gets its own message. Built
against a synthetic `%LOCALAPPDATA%` so all three are reachable without breaking
the real install.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp.extended import instance, platform  # noqa: E402

MIN_MB = platform.MIN_EXE_BYTES // (1024 * 1024)


def build_tree(root: str, layout: dict) -> None:
    """Create ``<root>/Roblox/Versions/<dir>/<files>``.

    A value of ``None`` creates the directory with no files; a list creates
    zero-byte files of those names, except an int, which becomes a file of that
    many megabytes.
    """
    versions = os.path.join(root, "Roblox", "Versions")
    for name, files in layout.items():
        target = os.path.join(versions, name)
        os.makedirs(target, exist_ok=True)
        for filename, size in (files or {}).items():
            path = os.path.join(target, filename)
            with open(path, "wb") as handle:
                if size:
                    handle.truncate(size * 1024 * 1024)


class FindStudioExeMessages(unittest.TestCase):
    def _find(self, layout):
        with tempfile.TemporaryDirectory() as root:
            build_tree(root, layout)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": root}), \
                 mock.patch.object(platform, "is_windows", return_value=True):
                try:
                    return None, instance.find_studio_exe()
                except FileNotFoundError as exc:
                    return str(exc), None

    def test_a_complete_install_is_found(self):
        message, exe = self._find({
            "version-a": {"RobloxStudioBeta.exe": 250},
        })
        self.assertIsNone(message)
        # Asserted component-wise, not with a literal `version-a\RobloxStudioBeta.exe`.
        # `is_windows` is mocked True so the Windows branch runs, but the path is
        # still assembled with the *host's* separator, so on the `macos-latest`
        # runner it came back joined with `/` and the hardcoded backslash failed.
        # What is being claimed is "the exe lives in the version directory", and
        # that is what the two components say.
        self.assertEqual(os.path.basename(exe), "RobloxStudioBeta.exe")
        self.assertEqual(os.path.basename(os.path.dirname(exe)), "version-a")

    def test_no_installs_at_all(self):
        message, _ = self._find({})
        self.assertIn("no Roblox install directory", message)
        self.assertIn("Install Studio", message)

    def test_player_only_tells_the_agent_waiting_will_not_help(self):
        """The measured case: a Player install is a normal machine, not a
        half-finished update. Telling the agent to wait is the wrong recovery."""
        message, _ = self._find({
            "version-p": {"RobloxPlayerBeta.exe": 140, "RobloxCrashHandler.exe": 1},
        })
        self.assertIn("Player", message)
        self.assertIn("waiting will not help", message)
        self.assertNotIn("update may be in progress", message)

    def test_partial_install_says_wait_because_that_is_right_here(self):
        message, _ = self._find({
            "version-u": {"RobloxStudioBeta.exe": 4, "RobloxStudioInstaller.exe": 1},
        })
        self.assertIn("half-extracted update", message)
        self.assertIn("wait for it", message)

    def test_broken_install_is_distinguished_from_an_absent_one(self):
        """Studio's own files present, executable gone. The measured case here was
        the executable being *renamed* - so "install Studio" would have been
        wrong, and so would "wait for an update"."""
        message, _ = self._find({
            "version-b": {"RobloxStudioInstaller.exe": 1, "NativeDialog.exe": 1,
                          "StudioMCP.exe": 6},
        })
        self.assertIn("broken install", message)
        self.assertIn("Restore the executable", message)
        self.assertNotIn("Install Studio from the Roblox website; waiting", message)

    def test_the_old_single_sentence_is_gone(self):
        """The exact string the old code emitted, in all three branches."""
        for layout in (
            {},
            {"version-p": {"RobloxPlayerBeta.exe": 140}},
            {"version-b": {"RobloxStudioInstaller.exe": 1}},
        ):
            message, _ = self._find(layout)
            self.assertNotIn(
                "An update may be in progress; wait for it to finish", message)


class DescribeInstalls(unittest.TestCase):
    def test_classifies_studio_player_and_remnant(self):
        with tempfile.TemporaryDirectory() as root:
            build_tree(root, {
                "version-s": {"RobloxStudioBeta.exe": 250},
                "version-p": {"RobloxPlayerBeta.exe": 140},
                "version-r": {"StudioMCP.exe": 6},
            })
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": root}):
                entries = {e["dir"]: e for e in platform.describe_installs()}
        self.assertTrue(entries["version-s"]["is_studio_install"])
        self.assertFalse(entries["version-p"]["is_studio_install"])
        self.assertIn("RobloxPlayerBeta.exe", entries["version-p"]["other_exes"])
        self.assertFalse(entries["version-r"]["is_studio_install"])

    def test_missing_versions_directory_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": root}):
                self.assertEqual(platform.describe_installs(), [])

    def test_studio_exe_still_finds_the_newest_complete_install(self):
        with tempfile.TemporaryDirectory() as root:
            build_tree(root, {
                "version-old": {"RobloxStudioBeta.exe": 250},
                "version-new": {"RobloxStudioBeta.exe": 250},
                "version-bad": {"RobloxStudioBeta.exe": 1},
            })
            versions = os.path.join(root, "Roblox", "Versions")
            # Make "version-new" unambiguously newer.
            for name, when in (("version-old", 1000), ("version-new", 2000)):
                os.utime(os.path.join(versions, name), (when, when))
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": root}), \
                 mock.patch.object(platform, "is_windows", return_value=True):
                exe = platform.studio_exe()
        self.assertIn("version-new", exe)


if __name__ == "__main__":
    unittest.main()
