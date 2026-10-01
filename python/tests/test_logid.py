"""Tests for reading a Studio process's identity out of its own log.

The strings below are the real shapes, taken from logs on this machine. The point
of each test is the *trap* it locks down, not the happy path: every one of these
is a way the parse can silently produce a plausible wrong answer.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import logid  # noqa: E402


def _log(body: str) -> str:
    """Wrap a body the way a real log frames it, so the banner regex is exercised
    against the surrounding noise rather than against a clean string."""
    return (
        "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] "
        "All use of Roblox services must comply with Roblox's Terms of Use\n"
        + body
        + "\n"
        "2026-09-30T09:12:05.831Z,0.831000,0edc,6,Info [FLog::Output] trailing line\n"
    )


FILE_BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe --task EditFile --localPlaceFile "
    r"C:\Users\User\AppData\Local\Temp\robloxstudio-mcp-baseplates\Baseplate-555883251.rbxl"
)

URI_BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe roblox-studio:1+task:EditPlace+placeId:95206881+universeId:28220420"
)

SERVER_BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe -placeVersion 0 -creatorId 0 -task StartServer "
    r"-localProjectFile C:/Users/User/AppData/Local/Temp/robloxstudio-mcp-baseplates/Baseplate-1340472086.rbxl"
)

CLIENT_BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe -task StartClient -rbxTransportToken bG9jYWxfdGVzdA== "
    r"-localProjectFile C:/Users/User/AppData/Local/Temp/robloxstudio-mcp-baseplates/Baseplate-1340472086.rbxl"
)

LAUNCHER_BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe -startEvent www.roblox.com/robloxQTStudioStartedEvent "
    r'-launchIntentString {"task":"None"} -parentPid 1234'
)

PID_LINE = (
    "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] "
    "Constructing UIThreadNotifier for process '16240' "
    "with id 'https://www.roblox.com-Studio'"
)

GUID_LINES = (
    "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] "
    "Session GUID is 45105BED-F1B1-4847-B213-FB0DD03F7E1B\n"
    "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] "
    "Machine GUID is 3FDE6FD7-3243-49EF-B961-0E81894BA3F0"
)


class ParsePid(unittest.TestCase):
    def test_reads_the_pid(self):
        self.assertEqual(logid.parse_identity(_log(PID_LINE))["pid"], 16240)

    def test_pid_absent_is_none_not_zero(self):
        """Zero is a real pid shape on some systems, so absence must not look like
        a found value; a caller would then join on a process that does not exist."""
        self.assertIsNone(logid.parse_identity(_log(FILE_BANNER))["pid"])

    def test_will_not_truncate_a_longer_process_name(self):
        """The regex ends at the closing quote. A name with digits after the pid
        must not yield a short wrong pid."""
        line = ("2026-09-30T09:16:54.175Z,1.1,1a78,6,Info [FLog::UIThreadNotifier] "
                "Constructing UIThreadNotifier for process '1234567890'")
        self.assertEqual(logid.parse_identity(_log(line))["pid"], 1234567890)


class ParseCommandLine(unittest.TestCase):
    def test_file_route_yields_the_full_path(self):
        got = logid.parse_identity(_log(FILE_BANNER))
        self.assertTrue(got["place_path"].endswith("Baseplate-555883251.rbxl"))
        self.assertEqual(got["task"], "EditFile")
        self.assertIsNone(got["place_id"])

    def test_uri_route_yields_the_ids_not_a_path(self):
        """The URI route carries the ids inline and no path at all, so treating it
        as a path launch would produce a match on nothing."""
        got = logid.parse_identity(_log(URI_BANNER))
        self.assertEqual(got["place_id"], 95206881)
        self.assertEqual(got["universe_id"], 28220420)
        self.assertIsNone(got["place_path"])
        self.assertEqual(got["task"], "EditPlace")

    def test_playtest_uses_local_project_file(self):
        """A play test's server and clients spell it ``-localProjectFile`` with one
        dash. Matching only ``--localPlaceFile`` left 8 of 45 real logs with no
        place, which is the whole play-test population."""
        for banner in (SERVER_BANNER, CLIENT_BANNER):
            got = logid.parse_identity(_log(banner))
            self.assertTrue(
                got["place_path"].endswith("Baseplate-1340472086.rbxl"),
                "expected the project file, got %r" % got["place_path"],
            )

    def test_playtest_roles_and_parent(self):
        server = logid.parse_identity(_log(SERVER_BANNER))
        client = logid.parse_identity(_log(CLIENT_BANNER))
        self.assertEqual(server["task"], "StartServer")
        self.assertEqual(client["task"], "StartClient")
        self.assertEqual(client["transport_token"], "bG9jYWxfdGVzdA==")

    def test_launcher_task_none_is_not_a_task(self):
        """``-launchIntentString {"task":"None"}`` is the launcher saying it has no
        task, which is different from the log saying nothing. Reporting "None" as
        the task would make a launch look identified."""
        got = logid.parse_identity(_log(LAUNCHER_BANNER))
        self.assertEqual(got["task"], "None")
        self.assertEqual(got["parent_pid"], 1234)
        self.assertIsNone(got["place_path"])

    def test_bare_executable_is_not_a_crash(self):
        got = logid.parse_identity(_log(
            r"C:\Users\User\AppData\Local\Roblox\Versions\version-6b0e880a1a144428"
            r"\RobloxStudioBeta.exe"
        ))
        self.assertIsNone(got["place_path"])
        self.assertIsNone(got["task"])

    def test_timestamped_lines_are_not_mistaken_for_a_banner(self):
        """Every ordinary log line is prefixed with a date, so the banner regex
        must not anchor loosely enough to match one and call it a command line."""
        got = logid.parse_identity(_log(
            "2026-09-30T09:12:05.347Z,0.347599,0edc,6 [FLog::Output] "
            "Creating PolicyContext(Root)"
        ))
        self.assertIsNone(got["place_path"])
        self.assertIsNone(got["task"])


class ParseGuids(unittest.TestCase):
    def test_session_and_machine_are_distinct_fields(self):
        got = logid.parse_identity(_log(GUID_LINES))
        self.assertEqual(got["session_guid"], "45105BED-F1B1-4847-B213-FB0DD03F7E1B")
        self.assertEqual(got["machine_guid"], "3FDE6FD7-3243-49EF-B961-0E81894BA3F0")

    def test_session_and_machine_cannot_be_confused(self):
        """Both are 36 hex chars in the same channel, so a swapped field is
        invisible. Machine GUID measured 2 distinct values across 45 logs, so
        treating it as per-process would be wrong."""
        text = _log(GUID_LINES + "\n" + PID_LINE)
        got = logid.parse_identity(text)
        self.assertNotEqual(got["session_guid"], got["machine_guid"])
        self.assertEqual(got["pid"], 16240)


class MatchMeshName(unittest.TestCase):
    def setUp(self):
        self.identities = {
            16240: {"place_path": r"C:\tmp\robloxstudio-mcp-baseplates\Baseplate-555883251.rbxl",
                    "place_id": None},
            12324: {"place_path": None, "place_id": 95206881},
            19500: {"place_path": None, "place_id": 95206881},
        }

    def test_file_route_matches_on_basename(self):
        self.assertEqual(
            logid.match_mesh_name("Baseplate-555883251.rbxl", self.identities), [16240]
        )

    def test_full_path_is_not_a_mesh_name(self):
        """The mesh reports the basename, so a full path must match nothing rather
        than falling through to some looser rule."""
        self.assertEqual(
            logid.match_mesh_name(r"C:\tmp\x\Baseplate-555883251.rbxl", self.identities), []
        )

    def test_uri_route_returns_every_candidate_not_one(self):
        """Two URI launches of one place are indistinguishable, and returning both
        is what lets the caller say so instead of picking one."""
        self.assertEqual(
            sorted(logid.match_mesh_name("Template_95206881_AutoRecovery_3.rbxl",
                                         self.identities)),
            [12324, 19500],
        )

    def test_no_name_matches_nothing(self):
        """A server and its clients all report ``name: null``, so this is a normal
        input, not an error."""
        self.assertEqual(logid.match_mesh_name(None, self.identities), [])

    def test_place_basename_wins_over_place_id(self):
        """A file launch's basename is exact, so a shared placeId elsewhere must not
        dilute it into a multi-candidate result."""
        self.assertEqual(
            logid.match_mesh_name("Baseplate-555883251.rbxl", self.identities), [16240]
        )


class Ambiguity(unittest.TestCase):
    """``ambiguous_reason(mesh_name, candidates, identities)``.

    The second argument is the **candidate PID list**, not a filtered list of the
    identities that happened to be readable. Passing the filtered list is the bug
    this signature exists to make impossible to write again - see
    ``test_unreadable_log_does_not_look_unambiguous``.
    """

    def test_single_readable_match_is_unambiguous(self):
        self.assertIsNone(
            logid.ambiguous_reason("a.rbxl", [16240], {16240: {"place_path": "a.rbxl"}})
        )

    def test_no_name_explains_the_null_case(self):
        reason = logid.ambiguous_reason(None, [], {})
        self.assertIn("no place name", reason)

    def test_uri_route_names_the_actual_obstacle(self):
        """The reason has to point at the AutoRecovery counter, because that is
        what actually blocks it and what a caller could otherwise work around."""
        reason = logid.ambiguous_reason(
            "Template_1_AutoRecovery_2.rbxl",
            [1, 2],
            {1: {"place_id": 1}, 2: {"place_id": 1}},
        )
        self.assertIn("AutoRecovery", reason)

    def test_several_matches_are_counted(self):
        reason = logid.ambiguous_reason(
            "a.rbxl", [1, 2, 3], {i: {"place_path": "a.rbxl"} for i in (1, 2, 3)}
        )
        self.assertIn("3 logs", reason)

    def test_no_match_says_so(self):
        self.assertIn(
            "no Studio log mentions", logid.ambiguous_reason("a.rbxl", [], {})
        )

    def test_unreadable_log_does_not_look_unambiguous(self):
        """The regression this signature was changed for.

        Two candidate processes, only one readable. Under the old contract the
        caller passed the readable identities, this function saw a list of length
        1, and returned ``None`` - reporting no ambiguity and no reason for a
        genuinely ambiguous case. Measured live 2026-10-01: ``resolved=False``
        with ``ambiguous=None``, which a caller cannot tell apart from "no such
        Studio".
        """
        reason = logid.ambiguous_reason(
            "Place1", [14080, 14788], {14080: {"place_path": "Place1"}}
        )
        self.assertIsNotNone(
            reason, "a missing log must not be absorbed into a false certainty"
        )
        self.assertIn("2 processes", reason)
        self.assertIn("only 1", reason)

    def test_one_candidate_with_unreadable_log_is_not_resolved(self):
        """One candidate is not automatically the answer - if its log is
        unreadable, the identity is unconfirmed rather than established."""
        reason = logid.ambiguous_reason("Place1", [16240], {})
        self.assertIsNotNone(reason)
        self.assertIn("could not be read", reason)


class NameMatchesIdentity(unittest.TestCase):
    """The launch direction: a process is known, the studio_id is wanted."""

    def test_file_launch_matches_exactly(self):
        identity = {"place_path": r"C:\tmp\Baseplate-9.rbxl", "place_id": None}
        self.assertTrue(logid.name_matches_identity("Baseplate-9.rbxl", identity))
        self.assertFalse(logid.name_matches_identity("Baseplate-8.rbxl", identity))

    def test_uri_launch_matches_on_the_prefix_only(self):
        """The counter is unknowable from the log, so the prefix is the honest
        assertion. A caller that accepts this must then check it is unique."""
        identity = {"place_path": None, "place_id": 95206881}
        self.assertTrue(
            logid.name_matches_identity("Template_95206881_AutoRecovery_7.rbxl", identity)
        )
        self.assertFalse(
            logid.name_matches_identity("Template_999_AutoRecovery_7.rbxl", identity)
        )

    def test_null_mesh_name_never_matches(self):
        self.assertFalse(logid.name_matches_identity(
            None, {"place_path": r"C:\tmp\a.rbxl", "place_id": 1}))

    def test_both_matchers_agree(self):
        """The two directions are separate code, so they can drift. Anything one
        accepts the other must too, or a launch resolves one way and a stop the
        other."""
        cases = [
            ("Baseplate-9.rbxl", {"place_path": r"C:\tmp\Baseplate-9.rbxl", "place_id": None}),
            ("Template_7_AutoRecovery_2.rbxl", {"place_path": None, "place_id": 7}),
            ("Baseplate-8.rbxl", {"place_path": r"C:\tmp\Baseplate-9.rbxl", "place_id": 7}),
            (None, {"place_path": r"C:\tmp\Baseplate-9.rbxl", "place_id": 7}),
        ]
        for name, identity in cases:
            by_name = bool(logid.match_mesh_name(name, {1: identity}))
            by_identity = logid.name_matches_identity(name, identity)
            self.assertEqual(by_name, by_identity, "disagreed on %r" % (name,))


class ProcessStart(unittest.TestCase):
    """``Get-CimInstance | ConvertTo-Json`` hands back ``/Date(ms)/``.

    An earlier version of the token join compared a log's start stamp to each
    process's creation time, and could not parse this format. It failed silently,
    skipping every process, which read as "no process matched" rather than as a
    parser bug.
    """

    def test_parses_the_cim_json_form(self):
        self.assertAlmostEqual(
            logid.parse_process_started("/Date(1790729920871)/"),
            1790729920.871,
            places=3,
        )

    def test_parses_an_iso_string(self):
        self.assertIsNotNone(logid.parse_process_started("2026-09-30T09:18:20"))

    def test_number_passes_through(self):
        self.assertEqual(logid.parse_process_started(1790729920.871), 1790729920.871)

    def test_unparseable_is_none_not_zero(self):
        """Zero is a real epoch, so a failure here would look like 1970 and put
        every log outside the window."""
        for value in ("", None, "not a date"):
            self.assertIsNone(logid.parse_process_started(value))


class WindowFallback(unittest.TestCase):
    """Narrowing by time must never change the answer, only the cost."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="logid-window-")
        self._restore = logid.log_dir
        logid.log_dir = lambda: self._dir

    def tearDown(self):
        # Restored before the files go, so a failure cannot leave log_dir pointing
        # at a deleted temp directory for the rest of the session.
        logid.log_dir = self._restore
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)

    def _write(self, name, pid, stamp_text):
        stamp = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
        path = os.path.join(self._dir, "0.741.19.7411056_%s_Studio_0000A_last.log" % stamp_text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                FILE_BANNER
                + "\n"
                + "2026-09-30T12:00:00.100Z,0.1,1a78,6,Info [FLog::UIThreadNotifier] "
                "Constructing UIThreadNotifier for process '%d' with id 'x'" % pid
            )
        return stamp

    def test_window_that_excludes_everything_still_falls_back(self):
        """A too-narrow window must cost time, not an identity. If this regressed,
        a live Studio would silently drop out of the join."""
        stamp = self._write("old", 4242, "20260930T120000Z")
        absurd = {4242: stamp.timestamp() - 86400 * 365}
        got = logid.live_identities([4242], absurd)
        self.assertEqual(got.get(4242, {}).get("pid"), 4242)

    def test_window_that_includes_the_log_finds_it(self):
        stamp = self._write("hit", 4343, "20260930T120000Z")
        got = logid.live_identities([4343], {4343: stamp.timestamp()})
        self.assertEqual(got.get(4343, {}).get("pid"), 4343)

    def test_no_window_is_still_a_full_sweep(self):
        self._write("plain", 4444, "20260930T120000Z")
        self.assertIn(4444, logid.live_identities([4444]))


class Stamp(unittest.TestCase):
    def test_parses_the_filename_stamp(self):
        seconds = logid.stamp_seconds("0.741.19.7411056_20260930T091820Z_Studio_D4ED5_last.log")
        self.assertIsNotNone(seconds)
        # 2026-09-30T09:18:20Z as epoch seconds, checked via the parsed value's own
        # UTC fields so the test does not depend on the host's timezone.
        import datetime as dt

        self.assertEqual(
            dt.datetime.fromtimestamp(seconds, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "2026-09-30T09:18:20",
        )

    def test_missing_stamp_is_none(self):
        self.assertIsNone(logid.stamp_seconds("RobloxStudioInstaller_0EA05.log"))


if __name__ == "__main__":
    unittest.main()
