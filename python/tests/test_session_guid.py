"""The AutoRecovery counter is in the log, on the path-suffixed PlaceSessionId.

This project twice asserted the counter was recorded in no log, and was wrong
both times: it appears on the ``PlaceSessionId`` line whose suffix is a *path*
rather than a place id. That line was also past the prefix the identity sweep
reads, so the sweep could not have seen it even if it had looked.

These tests pin the three things that make the URI route join: the two suffix
forms are distinguished, the counter is recovered from the path form, and a log
without an autorecovery document narrows nothing rather than guessing.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import logid  # noqa: E402

#: Verbatim shapes from pid 12324's log on this machine.
NUMERIC = (
    "a,b,c,6 [telemetryLog] PlaceSessionId: "
    "A3A73B66-0B94-4C5E-AA26-5F44551274BC-95206881"
)
PATH_FORM = (
    "a,b,c,6 [telemetryLog] PlaceSessionId: "
    "77C06B69-0B74-4C0B-B082-39E5D0EC16BB-"
    "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves"
    "\\Template_95206881_AutoRecovery_3.rbxl"
)
DM_ID = (
    "a,b,c,6 [telemetryLog] DmId: 77C06B69-0B74-4C0B-B082-39E5D0EC16BB-"
    "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves"
    "\\Template_95206881_AutoRecovery_3.rbxl-StudioGameStateType_Edit"
)
KEY_EVENT = (
    "x [FLog::StudioKeyEvents] open place (identifier = "
    "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves"
    "\\Template_95206881_AutoRecovery_3.rbxl) [start]"
)


class SessionGuids(unittest.TestCase):
    def test_both_forms_yield_their_guid(self):
        guids = logid.session_guids(NUMERIC + "\n" + PATH_FORM)
        self.assertIn("A3A73B66-0B94-4C5E-AA26-5F44551274BC", guids)
        self.assertIn("77C06B69-0B74-4C0B-B082-39E5D0EC16BB", guids)

    def test_dm_id_contributes_its_guid_too(self):
        guids = logid.session_guids(DM_ID)
        self.assertEqual(guids, ["77C06B69-0B74-4C0B-B082-39E5D0EC16BB"])

    def test_repeats_are_collapsed_but_order_kept(self):
        """One measured log had 29 PlaceSessionId lines carrying 2 distinct GUIDs.
        Returning 29 entries would be useless as an identity."""
        guids = logid.session_guids((NUMERIC + "\n") * 13)
        self.assertEqual(guids, ["A3A73B66-0B94-4C5E-AA26-5F44551274BC"])

    def test_no_guids_is_empty_not_none(self):
        self.assertEqual(logid.session_guids("nothing here"), [])


class PlaceSessionPath(unittest.TestCase):
    def test_path_form_yields_the_path(self):
        self.assertEqual(
            logid.place_session_path(PATH_FORM),
            "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves"
            "\\Template_95206881_AutoRecovery_3.rbxl",
        )

    def test_numeric_form_yields_none(self):
        """It names a published place, not a file, so there is no counter in it.
        Returning something here would invent a path that does not exist."""
        self.assertIsNone(logid.place_session_path(NUMERIC))

    def test_the_counter_is_recoverable_from_the_path(self):
        path = logid.place_session_path(PATH_FORM)
        self.assertIn("_AutoRecovery_3.rbxl", path)
        self.assertEqual(os.path.basename(path), "Template_95206881_AutoRecovery_3.rbxl")

    def test_empty_log_is_none(self):
        self.assertIsNone(logid.place_session_path(""))


class PrefixDepth(unittest.TestCase):
    def test_prefix_covers_the_place_session_lines(self):
        """Measured: numeric-form PlaceSessionId lines sit at 64,520 to 74,960
        bytes. A 64 KB prefix missed every one, which is how two logs of this
        project came to say the counter was not recorded at all."""
        self.assertGreaterEqual(logid.PREFIX_BYTES, 81920)

    def test_prefix_is_not_so_large_it_stops_being_cheap(self):
        """Per-file cost is 0.100 ms at 64 KB, 0.130 ms at 256 KB, 0.626 ms at
        1 MB. 256 KB is the knee; past it the sweep cost multiplies."""
        self.assertLessEqual(logid.PREFIX_BYTES, 512 * 1024)


class Refinement(unittest.TestCase):
    """The ambiguous URI case must narrow on evidence, or decline to narrow."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="logid-refine-")
        self._restore = logid.log_dir
        logid.log_dir = lambda: self._dir

    def tearDown(self):
        logid.log_dir = self._restore
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)

    def _log(self, name, body):
        with open(os.path.join(self._dir, name), "w", encoding="utf-8") as handle:
            handle.write(body)

    def test_two_uri_launches_are_separated_by_the_counter(self):
        """The case this project called unresolvable. Both candidates share a
        placeId; only one log records the autorecovery path, and that is the one
        the mesh name refers to."""
        self._log("a.log", PATH_FORM)
        self._log("b.log", NUMERIC)
        identities = {
            111: {"place_id": 95206881, "log": "a.log"},
            222: {"place_id": 95206881, "log": "b.log"},
        }
        self.assertEqual(
            logid.match_mesh_name("Template_95206881_AutoRecovery_3.rbxl", identities),
            [111],
        )

    def test_a_counter_no_log_records_keeps_the_candidates(self):
        """A mesh name whose counter matches nothing must not resolve to nothing.

        Returning ``[]`` would claim no such Studio exists, which is a worse wrong
        answer than "I cannot tell which of these two it is". The caller gets both
        and decides.
        """
        self._log("a.log", PATH_FORM)   # records counter 3
        self._log("b.log", NUMERIC)     # records no counter
        identities = {
            111: {"place_id": 95206881, "log": "a.log"},
            222: {"place_id": 95206881, "log": "b.log"},
        }
        self.assertEqual(
            sorted(logid.match_mesh_name("Template_95206881_AutoRecovery_9.rbxl", identities)),
            [111, 222],
        )

    def test_no_path_in_any_log_keeps_the_full_candidate_list(self):
        """Both opened a plain unsaved document, so neither has a counter.
        Narrowing to one here would be a coin flip presented as a match."""
        self._log("a.log", NUMERIC)
        self._log("b.log", NUMERIC)
        identities = {
            111: {"place_id": 95206881, "log": "a.log"},
            222: {"place_id": 95206881, "log": "b.log"},
        }
        self.assertEqual(
            sorted(logid.match_mesh_name("Template_95206881_AutoRecovery_3.rbxl", identities)),
            [111, 222],
        )

    def test_a_single_candidate_is_not_refined(self):
        """No reason to do a whole-file read when the answer is already unique."""
        self._log("a.log", NUMERIC)
        identities = {111: {"place_id": 95206881, "log": "a.log"}}
        self.assertEqual(
            logid.match_mesh_name("Template_95206881_AutoRecovery_3.rbxl", identities),
            [111],
        )


if __name__ == "__main__":
    unittest.main()
