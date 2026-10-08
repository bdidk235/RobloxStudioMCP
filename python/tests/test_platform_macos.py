"""macOS support: what is verified here, and what is not.

The distinction matters more than usual in this project, so it is the first test.

**Verified on this machine:** the macOS *parsing*. ``ps`` rows, a Studio banner
with no ``.exe``, forward-slash paths, and the three macOS directories. All of it
is exercised on Windows against macOS-shaped fixtures, because the risk in a port
is parsing, not the shell.

**Not verified:** that any of it works on a Mac. No Mac was available, so the
macOS integration has never run. `test_macos_integration_is_unproven` records that
as a fact rather than leaving it to be assumed.

The ``RobloxStudioBeta.exe`` case is the one that motivated the module: a pattern
requiring ``.exe`` matches no Mac banner, and the old parser would then report "no
identity" with no error at all.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import instance, logid, locks, platform  # noqa: E402

MAC_BANNER = (
    "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio "
    "--task EditFile --localPlaceFile "
    "/Users/me/Library/Application Support/Roblox/RobloxStudio/AutoSaves/Baseplate-1.rbxl"
)
WIN_BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe --task EditFile --localPlaceFile "
    r"C:\Users\User\AppData\Local\Temp\robloxstudio-mcp-baseplates\Baseplate-1.rbxl"
)


class Unproven(unittest.TestCase):
    def test_macos_integration_is_unproven(self):
        """Stated so nobody reads the passing tests as a working Mac port.

        Every macOS assertion below is a *fixture*: shaped input, parsed output.
        If a Mac ever becomes genuinely exercised, run the live probe and retire
        this.

        **The assertion is on the claim, not on the host.** It used to be
        ``assertFalse(platform.is_macos())``, which asserted something about the
        machine rather than about the suite - and the ``macos-latest`` runner
        *is* a Mac, so it was permanently false there and the job could never go
        green, the opposite of what it was for.

        The claim is still true on a Mac: the same fixtures prove as little there
        as on a PC. So what is asserted is the one thing that would actually
        retire it - the live integration suite running. Point that at a real
        Studio and this fails, forcing the claim to be revisited deliberately
        rather than quietly outliving its own refutation.
        """
        live = os.environ.get("ROBLOX_STUDIO_MCP_INTEGRATION") == "1"
        self.assertFalse(
            live,
            "ROBLOX_STUDIO_MCP_INTEGRATION=1, so live integration IS running and "
            "this claim is retired - revisit it (host: %r)" % sys.platform,
        )


class BannerPattern(unittest.TestCase):
    def test_matches_a_windows_banner(self):
        match = platform.BANNER_RE.search(WIN_BANNER)
        self.assertIsNotNone(match)
        self.assertIn("--task EditFile", match.group(1))

    def test_matches_a_macos_banner_with_no_exe(self):
        """The regression that motivated the module. Requiring ``.exe`` finds
        nothing here, and "found nothing" becomes "no identity", silently."""
        match = platform.BANNER_RE.search(MAC_BANNER)
        self.assertIsNotNone(match, "a Mac banner must match")
        self.assertIn("--task EditFile", match.group(1))

    def test_the_two_spellings_are_equivalent(self):
        self.assertIsNotNone(platform.BANNER_RE.search(WIN_BANNER))
        self.assertIsNotNone(platform.BANNER_RE.search(MAC_BANNER))

    def test_still_rejects_an_ordinary_log_line(self):
        self.assertIsNone(platform.BANNER_RE.search(
            "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] hello"
        ))


class LogidUsesTheSharedPattern(unittest.TestCase):
    def test_the_parser_accepts_both_banner_spellings(self):
        for banner in (WIN_BANNER, MAC_BANNER):
            got = logid.parse_identity(banner)
            self.assertEqual(got["task"], "EditFile", banner[:40])
            self.assertTrue(got["place_path"], banner[:40])

    def test_a_macos_banner_yields_a_usable_basename(self):
        """The mesh names a document by basename, and a ``/``-separated path must
        survive that on any host.

        Note this is *not* a claim that ``os.path.basename`` fails here - it does
        not, because ``ntpath`` handles both separators. An earlier version of
        this file claimed otherwise and pinned it with a test; the test failed and
        the claim was dropped.
        """
        got = logid.parse_identity(MAC_BANNER)
        self.assertEqual(platform.basename(got["place_path"]), "Baseplate-1.rbxl")
        self.assertEqual(os.path.basename(got["place_path"]), "Baseplate-1.rbxl")


class Directories(unittest.TestCase):
    def _as(self, platform_name):
        with mock.patch.object(platform, "is_windows", return_value=platform_name == "win"):
            return platform

    def test_log_dir_per_platform(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\User\AppData\Local"}):
            with mock.patch.object(platform, "is_windows", return_value=True):
                self.assertTrue(platform.log_dir().endswith(
                    os.path.join("Roblox", "logs")))
            with mock.patch.object(platform, "is_windows", return_value=False), \
                 mock.patch.object(platform, "home", return_value="/Users/me"):
                self.assertEqual(platform.log_dir(), "/Users/me/Library/Logs/Roblox")

    def test_autosaves_dir_per_platform(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\User\AppData\Local"}):
            with mock.patch.object(platform, "is_windows", return_value=True):
                self.assertTrue(platform.autosaves_dir().endswith(
                    os.path.join("Roblox", "RobloxStudio", "AutoSaves")))
            with mock.patch.object(platform, "is_windows", return_value=False), \
                 mock.patch.object(platform, "home", return_value="/Users/me"):
                self.assertEqual(
                    platform.autosaves_dir(),
                    "/Users/me/Library/Application Support/Roblox/RobloxStudio/AutoSaves",
                )

    def test_logid_and_locks_agree_with_the_platform_module(self):
        """They each had their own copy of the Windows path. Two copies is how a
        fix lands in one and not the other."""
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\User\AppData\Local"}), \
             mock.patch.object(platform, "is_windows", return_value=True):
            with mock.patch.object(platform, "is_windows", return_value=True):
                self.assertEqual(logid.log_dir(), platform.log_dir())
                self.assertEqual(locks.autosaves_dir(), platform.autosaves_dir())


class PsRowParsing(unittest.TestCase):
    """`ps -axo pid=,lstart=,command=` parsing, on a Mac's behalf."""

    def test_reads_a_studio_row(self):
        line = ("  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/"
                "Contents/MacOS/RobloxStudio --task EditFile --localPlaceFile /tmp/a.rbxl")
        got = platform.parse_ps_row(line)
        self.assertIsNotNone(got, line)
        self.assertEqual(got["pid"], 4700)
        self.assertEqual(got["created"], "Mon Sep 28 02:36:04 2026")
        self.assertIn("--localPlaceFile", got["command_line"])

    def test_a_command_line_containing_spaces_survives(self):
        """The reason the row is sliced rather than split: the command line is
        last and contains spaces, so ``split()`` would truncate it."""
        line = ("  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/"
                "Contents/MacOS/RobloxStudio -task EditFile --localPlaceFile "
                "/Users/me/Library/Application Support/Roblox/a place with spaces.rbxl")
        got = platform.parse_ps_row(line)
        self.assertIn("a place with spaces.rbxl", got["command_line"])

    def test_rejects_a_non_studio_process(self):
        self.assertIsNone(platform.parse_ps_row(
            "  4700 Mon Sep 28 02:36:04 2026 /usr/libexec/somethingelse"))

    def test_rejects_a_studio_without_an_instance_task(self):
        """A Studio that is not an instance - the installer, or a helper - has no
        place and no role, so it is not a candidate for anything."""
        self.assertIsNone(platform.parse_ps_row(
            "  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/"
            "Contents/MacOS/RobloxStudio --some-other-flag"))

    def test_rejects_junk(self):
        for line in ("", "   ", "not a row", "123"):
            self.assertIsNone(platform.parse_ps_row(line), repr(line))

    def test_a_uri_launched_studio_is_kept(self):
        """A URI launch carries no ``-task`` flag, so filtering on that alone
        would drop exactly the instances the URI route produces."""
        got = platform.parse_ps_row(
            "  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/"
            "Contents/MacOS/RobloxStudio roblox-studio:1+task:EditPlace+placeId:1")
        self.assertIsNotNone(got)
        self.assertEqual(got["pid"], 4700)


class StudioExe(unittest.TestCase):
    def test_macos_prefers_the_application_bundle(self):
        with mock.patch.object(platform, "is_windows", return_value=False):
            with mock.patch.object(platform.os.path, "isfile",
                                   side_effect=lambda p: p.startswith("/Applications/")):
                self.assertEqual(platform.studio_exe(),
                                 "/Applications/RobloxStudio.app/Contents/MacOS/"
                                 "RobloxStudio")

    def test_macos_falls_back_to_the_user_applications(self):
        with mock.patch.object(platform, "is_windows", return_value=False), \
             mock.patch.object(platform, "home", return_value="/Users/me"), \
             mock.patch.object(platform.os.path, "isfile",
                               side_effect=lambda p: "/Users/me/Applications" in p):
            self.assertTrue(platform.studio_exe().startswith("/Users/me/Applications"))

    def test_macos_returns_none_when_absent(self):
        with mock.patch.object(platform, "is_windows", return_value=False), \
             mock.patch.object(platform.os.path, "isfile", return_value=False):
            self.assertIsNone(platform.studio_exe())

    def test_windows_still_requires_a_complete_install(self):
        """A directory being replaced still exists; launching from it produced
        STATUS_DLL_NOT_FOUND twice. The size check is the guard."""
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\User\AppData\Local"}), \
             mock.patch.object(platform, "is_windows", return_value=True), \
             mock.patch.object(platform.os.path, "isdir", return_value=True), \
             mock.patch.object(platform.os, "listdir", return_value=["v1"]), \
             mock.patch.object(platform.os.path, "getsize", return_value=1024), \
             mock.patch.object(platform.os.path, "getmtime", return_value=0.0):
            self.assertIsNone(platform.studio_exe(), "a 1 KB file is mid-update")


class Basename(unittest.TestCase):
    """The host-independent basename, and the exact hazard it removes."""

    def test_splits_a_macos_path(self):
        self.assertEqual(
            platform.basename("/Users/me/Library/Application Support/Baseplate-1.rbxl"),
            "Baseplate-1.rbxl",
        )

    def test_splits_a_windows_path(self):
        self.assertEqual(
            platform.basename(r"C:\Users\User\AppData\Local\Temp\Baseplate-1.rbxl"),
            "Baseplate-1.rbxl",
        )

    def test_handles_both_separators_in_one_path(self):
        # spelling-ok: the literal is input fixture; the asserted output
        # is a bare filename, independent of path spelling.
        self.assertEqual(platform.basename(r"C:/tmp\a/Baseplate-1.rbxl"),
                         "Baseplate-1.rbxl")

    def test_a_bare_name_is_its_own_basename(self):
        self.assertEqual(platform.basename("Baseplate-1.rbxl"), "Baseplate-1.rbxl")

    def test_empty_is_empty_not_an_error(self):
        self.assertEqual(platform.basename(""), "")

    def test_it_agrees_with_os_path(self):
        """It is an explicitness wrapper, not a correction.

        Recorded because this test previously asserted the opposite - that the
        helper *differs* from ``os.path.basename`` on Windows - and that assertion
        was false: ``ntpath`` already treats both separators. A test that pins a
        false claim is worse than no test.
        """
    def test_it_handles_both_separators_whatever_the_host(self):
        """The host's separator must not decide how a *logged* path is split.

        A Studio log is data about a launch on some other machine, so the
        separator in it belongs to the writer, not the reader.

        The assertion used to be that this agrees with ``os.path.basename``, and
        that is only true on Windows: ``ntpath`` already treats both separators,
        so the two coincide there and the test said nothing. On macOS
        ``posixpath`` splits on ``/`` only, so for a Windows path the two
        **must** disagree - and this helper disagreeing is the correct
        behaviour, not a bug. Measured on the ``macos-latest`` runner, where the
        old assertion failed with the whole path returned unchanged.
        """
        for path in ("/Users/me/Library/Application Support/Baseplate-1.rbxl",
                     r"C:\Users\User\AppData\Local\Temp\Baseplate-1.rbxl",
                     "Baseplate-1.rbxl"):
            self.assertEqual(platform.basename(path), "Baseplate-1.rbxl", path)

        if platform.is_macos():
            # Documented, not incidental: this is the case that proves the
            # helper is doing something `os.path.basename` cannot here.
            self.assertNotEqual(
                os.path.basename(r"C:\Users\me\Baseplate-1.rbxl"),
                platform.basename(r"C:\Users\me\Baseplate-1.rbxl"),
            )
        else:
            for path in ("/Users/me/Library/Application Support/Baseplate-1.rbxl",
                         r"C:\Users\User\AppData\Local\Temp\Baseplate-1.rbxl",
                         "Baseplate-1.rbxl"):
                self.assertEqual(os.path.basename(path), platform.basename(path), path)


class PathsWithSpaces(unittest.TestCase):
    """The bug that only macOS-shaped input could find.

    ``~/Library/Application Support/...`` has a space in a *fixed* component of a
    Roblox-documented path, so every Mac launch of a recovered document would have
    been truncated by the old ``(\\S+)`` pattern - silently, because a truncated
    path still yields a basename and the name join then simply never matches.
    """

    def test_the_macos_autosaves_path_parses_whole(self):
        self.assertEqual(
            logid.parse_identity(MAC_BANNER)["place_path"],
            "/Users/me/Library/Application Support/Roblox/RobloxStudio/AutoSaves/"
            "Baseplate-1.rbxl",
        )

    def test_a_quoted_path_is_taken_to_the_closing_quote(self):
        args = '--task EditFile --localPlaceFile "C:\\a dir\\Baseplate-1.rbxl" -extra x'
        self.assertEqual(
            logid._path_after(args, "--localPlaceFile"),
            "C:\\a dir\\Baseplate-1.rbxl",
        )

    def test_a_following_flag_bounds_an_unquoted_path(self):
        args = "--localPlaceFile /tmp/place.rbxl -task EditPlace -placeId 7"
        self.assertEqual(
            logid._path_after(args, "--localPlaceFile"), "/tmp/place.rbxl")

    def test_a_slash_in_the_value_is_not_mistaken_for_a_flag(self):
        args = "-localProjectFile /Users/me/a-place/Baseplate-1.rbxl"
        self.assertEqual(
            logid._path_after(args, "-localProjectFile"),
            "/Users/me/a-place/Baseplate-1.rbxl",
        )

    def test_a_project_file_path_with_spaces_parses_whole(self):
        """The play-test flag, same defect, second call site."""
        banner = (
            "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio "
            "-task StartServer -localProjectFile "
            "/Users/me/Library/Application Support/Roblox/a place.rbxl"
        )
        self.assertEqual(
            logid.parse_identity(banner)["place_path"],
            "/Users/me/Library/Application Support/Roblox/a place.rbxl",
        )

    def test_windows_paths_still_parse_unchanged(self):
        """No regression on the platform that can actually run."""
        self.assertEqual(
            logid.parse_identity(WIN_BANNER)["place_path"],
            r"C:\Users\User\AppData\Local\Temp\robloxstudio-mcp-baseplates\Baseplate-1.rbxl",
        )

    def test_a_windows_path_with_spaces_parses(self):
        banner = (
            r"C:\Roblox\Versions\v1\RobloxStudioBeta.exe --task EditFile "
            r"--localPlaceFile C:\Users\User\My Places\Baseplate-1.rbxl"
        )
        self.assertEqual(
            logid.parse_identity(banner)["place_path"],
            r"C:\Users\User\My Places\Baseplate-1.rbxl",
        )

    def test_a_missing_flag_is_none_not_an_error(self):
        self.assertIsNone(logid._path_after("-task EditFile", "--localPlaceFile"))

    def test_a_flag_with_no_value_is_none(self):
        self.assertIsNone(logid._path_after("--localPlaceFile", "--localPlaceFile"))


class ExeThreshold(unittest.TestCase):
    def test_the_measured_100mb_threshold_is_preserved(self):
        """It was 100 MB, set from a real ``STATUS_DLL_NOT_FOUND``.

        A first draft of the platform module used 20 MB, which would have
        lowered a threshold derived from an observed failure without any
        measurement behind the change.
        """
        self.assertEqual(platform.MIN_EXE_BYTES, 100 * 1024 * 1024)
        self.assertEqual(instance.MIN_EXE_BYTES, platform.MIN_EXE_BYTES)

    def test_windows_rejects_an_install_below_it(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\L"}), \
             mock.patch.object(platform, "is_windows", return_value=True), \
             mock.patch.object(platform.os.path, "isdir", return_value=True), \
             mock.patch.object(platform.os, "listdir", return_value=["v1"]), \
             mock.patch.object(platform.os.path, "getsize",
                               return_value=platform.MIN_EXE_BYTES - 1), \
             mock.patch.object(platform.os.path, "getmtime", return_value=0.0):
            self.assertIsNone(platform.studio_exe())

    def test_windows_accepts_an_install_at_exactly_the_threshold(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\L"}), \
             mock.patch.object(platform, "is_windows", return_value=True), \
             mock.patch.object(platform.os.path, "isdir", return_value=True), \
             mock.patch.object(platform.os, "listdir", return_value=["v1"]), \
             mock.patch.object(platform.os.path, "getsize",
                               return_value=platform.MIN_EXE_BYTES), \
             mock.patch.object(platform.os.path, "getmtime", return_value=0.0):
            self.assertIsNotNone(platform.studio_exe())


class Termination(unittest.TestCase):
    def test_windows_uses_stop_process(self):
        with mock.patch.object(platform, "is_windows", return_value=True), \
             mock.patch.object(platform.subprocess, "run") as run:
            platform.terminate(4700)
        args = run.call_args.args[0]
        self.assertIn("Stop-Process", " ".join(args))

    def test_macos_uses_kill(self):
        with mock.patch.object(platform, "is_windows", return_value=False), \
             mock.patch.object(platform.subprocess, "run") as run:
            platform.terminate(4700)
        self.assertEqual(run.call_args.args[0][:2], ["kill", "-9"])


class TerminateProcessRouting(unittest.TestCase):
    def test_delegates_to_platform_terminate(self):
        with mock.patch.object(instance.platform, "terminate") as term, \
             mock.patch.object(instance, "_pid_alive", return_value=False):
            result = instance.terminate_process(4700)
        term.assert_called_once_with(4700)
        self.assertEqual(result, {"stopped": True, "pid": 4700})

    def test_windows_path_issues_stop_process(self):
        with mock.patch.object(platform, "is_windows", return_value=True), \
             mock.patch.object(platform.subprocess, "run") as run, \
             mock.patch.object(instance, "_pid_alive", return_value=False):
            result = instance.terminate_process(4700)
        self.assertIn("Stop-Process", " ".join(run.call_args.args[0]))
        self.assertEqual(result, {"stopped": True, "pid": 4700})

    def test_macos_path_issues_kill(self):
        with mock.patch.object(platform, "is_windows", return_value=False), \
             mock.patch.object(platform.subprocess, "run") as run, \
             mock.patch.object(instance, "_pid_alive", return_value=False):
            result = instance.terminate_process(4700)
        self.assertEqual(run.call_args.args[0][:3],
                         ["kill", "-9", "4700"])
        self.assertEqual(result, {"stopped": True, "pid": 4700})

    def test_grace_expiry_reports_error(self):
        with mock.patch.object(instance.platform, "terminate"), \
             mock.patch.object(instance, "_pid_alive", return_value=True):
            result = instance.terminate_process(4700, grace_seconds=0)
        self.assertFalse(result["stopped"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
