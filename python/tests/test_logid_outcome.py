"""Tests for the place-open outcome parser.

The shapes below are verbatim from logs on this machine. The traps are specific:
a Studio retries, so a naive "first failure wins" reports a launch as broken when
it recovered, and `Hang In Progress` is not a hang.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import logid  # noqa: E402

FAIL_FETCH = (
    "a,b,c,6 [telemetryLog] State: OpenPlaceInitialization\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceCreateDataModel\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceLoadDataModel\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceFailure\n"
    "a,b,c,6 [telemetryLog] ErrorType: DataModelLoadingFailure\n"
    "a,b,c,6 [telemetryLog] ErrorMessage: Error fetching latest place version\n"
    "a,b,c,6 [telemetryLog] TaskNames: OpenPlaceFailure;\n"
)

FAIL_CONN = (
    "a,b,c,6 [telemetryLog] State: OpenPlaceFailure\n"
    "a,b,c,6 [telemetryLog] ErrorType: DataModelLoadingFailure\n"
    "a,b,c,6 [telemetryLog] ErrorMessage: Connection error 279\n"
)

SUCCESS = "a,b,c,6 [telemetryLog] State: OpenPlaceSuccess\n"

#: The real tail of a successful Edit launch. ``OpenPlacePreSuccess`` is a
#: progress state whose name ends in "Success", and the sequence continues past
#: the terminal state to ``PlaceIdle`` - both of which break a naive parse.
PROGRESS_THEN_SUCCESS = (
    "a,b,c,6 [telemetryLog] State: OpenPlaceInitialization\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceCreateDataModel\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceLoadDataModel\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceWaitForStreaming\n"
    "a,b,c,6 [telemetryLog] State: OpenPlacePostLoadDataModel\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceEnterDataModelScope\n"
    "a,b,c,6 [telemetryLog] State: OpenPlacePreSuccess\n"
    "a,b,c,6 [telemetryLog] State: OpenPlaceSuccess\n"
    "a,b,c,6 [telemetryLog] Workflow: OpenPlace\n"
    "a,b,c,6 [telemetryLog] State: PlaceIdle\n"
)

HANG_IN_PROGRESS = (
    "x,Warning [FLog::StudioHangMonitor] Hang In Progress. HangId: 1, "
    "ServiceId: MainThreadHangs\n"
)
HANG_DETECTED = (
    "x,Warning [FLog::StudioHangMonitor] Hang Detected. HangId: 1\n"
)
SIGNED_IN = (
    "x [FLog::LoginController] Login got Standalone DM ready to enter User scope\n"
)
INSTANCES = "x [FLog::SystemCheck] Running instance count at launch 5\n"


class Outcome(unittest.TestCase):
    def test_reads_the_failure_reason(self):
        got = logid.open_outcome(FAIL_FETCH)
        self.assertEqual(got["state"], "OpenPlaceFailure")
        self.assertEqual(got["error_type"], "DataModelLoadingFailure")
        self.assertEqual(got["error_message"], "Error fetching latest place version")
        self.assertFalse(got["opened"])

    def test_distinguishes_the_two_failure_kinds(self):
        """Measured 3:1 across four real failures. Conflating them would send the
        reader after the wrong problem - a transport retry against a place fetch."""
        self.assertEqual(
            logid.open_outcome(FAIL_FETCH)["error_message"],
            "Error fetching latest place version",
        )
        self.assertEqual(
            logid.open_outcome(FAIL_CONN)["error_message"], "Connection error 279"
        )

    def test_a_real_success_sequence_is_read_as_success(self):
        """The measured tail of a successful launch. Reporting ``PlaceIdle`` as
        the state made 21 successful launches look like zero opened, because the
        sequence does not stop at the terminal state."""
        got = logid.open_outcome(PROGRESS_THEN_SUCCESS)
        self.assertTrue(got["opened"])
        self.assertEqual(got["state"], "OpenPlaceSuccess")

    def test_pre_success_is_not_a_success(self):
        """``OpenPlacePreSuccess`` ends with "Success" and is a progress state. A
        suffix test would call a half-finished load a success, which is the worst
        possible direction to be wrong in."""
        got = logid.open_outcome(
            "a,b,c,6 [telemetryLog] State: OpenPlacePreSuccess\n"
            "a,b,c,6 [telemetryLog] State: PlaceIdle\n"
        )
        self.assertFalse(got["opened"])
        self.assertIsNone(got["state"])

    def test_place_idle_after_a_failure_does_not_clear_it(self):
        """Idle is a progress state, so it must not overwrite a recorded failure."""
        got = logid.open_outcome(FAIL_FETCH + "a,b,c,6 [telemetryLog] State: PlaceIdle\n")
        self.assertFalse(got["opened"])
        self.assertEqual(got["error_message"], "Error fetching latest place version")

    def test_a_later_success_clears_an_earlier_failure(self):
        """Studio retries. First-failure-wins would report a healthy process as
        broken, which is worse than reporting nothing."""
        got = logid.open_outcome(FAIL_FETCH + FAIL_FETCH + SUCCESS)
        self.assertTrue(got["opened"])
        self.assertEqual(got["state"], "OpenPlaceSuccess")
        self.assertIsNone(got["error_message"])
        self.assertIsNone(got["error_type"])

    def test_the_last_failure_wins_when_it_never_recovers(self):
        got = logid.open_outcome(FAIL_FETCH + FAIL_CONN)
        self.assertEqual(got["error_message"], "Connection error 279")

    def test_hang_in_progress_is_not_a_hang(self):
        """This appears in logs that opened their place fine, including one with
        5 instances already running. Treating it as a hang would invent a fault
        on a healthy Studio."""
        got = logid.open_outcome(HANG_IN_PROGRESS + SUCCESS)
        self.assertTrue(got["opened"])
        self.assertEqual(got["state"], "OpenPlaceSuccess")
        self.assertIsNone(got["error_message"])

    def test_hang_in_progress_does_not_clear_a_failure(self):
        got = logid.open_outcome(FAIL_FETCH + HANG_IN_PROGRESS)
        self.assertEqual(got["error_message"], "Error fetching latest place version")

    def test_signed_in_and_instance_count(self):
        got = logid.open_outcome(SIGNED_IN + INSTANCES + SUCCESS)
        self.assertTrue(got["signed_in"])
        self.assertEqual(got["instances_at_launch"], 5)

    def test_never_signed_in_is_distinguishable(self):
        """Two real logs never reached sign-in and spun on 403s. That is a
        different failure from a place that would not fetch, and it is visible."""
        got = logid.open_outcome("x [DFLog::HttpTraceError] status:403 Forbidden\n")
        self.assertFalse(got["signed_in"])
        self.assertIsNone(got["state"])
        self.assertIsNone(got["instances_at_launch"])

    def test_empty_log_is_all_none_not_an_error(self):
        got = logid.open_outcome("")
        self.assertIsNone(got["state"])
        self.assertIsNone(got["error_message"])
        self.assertFalse(got["opened"])

    def test_error_message_is_not_matched_outside_a_failure(self):
        """Without the failure gate, any stray ErrorMessage line would attach
        itself to whatever the last state happened to be."""
        got = logid.open_outcome(
            SUCCESS + "a,b,c,6 [telemetryLog] ErrorMessage: unrelated later noise\n"
        )
        self.assertIsNone(got["error_message"])


class ReadOutcome(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="logid-outcome-")

    def tearDown(self):
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)

    def test_reads_past_the_identity_prefix(self):
        """The outcome lines sit wherever the load finished, not in the first
        4 KB. A prefix read would report 'no outcome' and that is the whole
        question this function answers."""
        path = os.path.join(self._dir, "deep.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("padding line\n" * 200000)  # ~3 MB, well past any prefix
            handle.write(FAIL_FETCH)
        got = logid.read_open_outcome(path)
        self.assertEqual(got["error_message"], "Error fetching latest place version")

    def test_missing_file_is_none(self):
        self.assertIsNone(
            logid.read_open_outcome(os.path.join(self._dir, "absent.log"))
        )


if __name__ == "__main__":
    unittest.main()
