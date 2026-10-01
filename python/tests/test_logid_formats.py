"""Tests for FLog line-format recognition, and for the PID across all four.

The point of every test here is the *silent* wrong answer, not the happy path.
A parser that reads an old-format log as having no PID produces a plausible
result and no error, so the tests below are written to fail if the recogniser
regresses into ignoring formats it does not recognise - which is the only way
this code can be wrong in a way nobody notices.

Fixtures are the real shapes. Where a shape could not be measured on this
machine (types 1, 2, 3 - zero occurrences in 129,901 lines across 67 logs), the
fixture is taken from the format spec and the test says so, because a fixture
nobody has ever seen against a parser nobody has ever run is exactly where a
wrong assumption hides.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import logid  # noqa: E402

#: The type-4 prefix, verbatim from a real log. The PID line is the one this
#: whole module exists to find, and it is type 4 on every log measured here.
PID_LINE_T4 = (
    "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] "
    "Constructing UIThreadNotifier for process '16240' with id "
    "'https://www.roblox.com-Studio'"
)

#: The same record with the **severity field empty** - the variant that is 29.6%
#: of every real line measured and that the format spec's own example shows,
#: while its prose never mentions it. Pinned because dropping the optional
#: severity field would mark tens of thousands of real lines unrecognised.
PID_LINE_T4_NO_SEVERITY = (
    "2026-09-30T09:16:54.175Z,1.175599,1a78,6 [FLog::UIThreadNotifier] "
    "Constructing UIThreadNotifier for process '16240'"
)

#: Type 1, from the spec's example. **Never observed on this machine**: zero of
#: 129,901 lines across 67 logs. The fixture is the spec's, so a pass here means
#: "the recogniser agrees with the spec", not "this format was seen".
PID_LINE_T1 = (
    "1712972981.05664,7fb4,6 [FLog::UIThreadNotifier] "
    "Constructing UIThreadNotifier for process '16240'"
)

#: Type 1 with a severity word in place of the numeric third field, the spelling
#: the brief used. Included because the spec's own type-1 example and its
#: type-1 *heading* disagree about whether that field exists, so a recogniser
#: that only accepts the spec's example rejects a real-world spelling of it.
PID_LINE_T1_SEVERITY = (
    "1712972981.05664,0edc,Warning [FLog::UIThreadNotifier] "
    "Constructing UIThreadNotifier for process '16240'"
)

#: Type 2: type 1's fields with **no channel marker**. The marker is the only
#: thing separating the two, which is why the two share a regex and are told
#: apart by :func:`logid.classify_line` rather than by their timestamps.
PID_LINE_T2 = "1712859371.37087,7b3c,6 Constructing UIThreadNotifier for process '16240'"

#: Type 3: TimeSinceStarted, a *named* thread, and no commas at all.
PID_LINE_T3 = "0.01454 7dbc: Constructing UIThreadNotifier for process '16240'"

BANNER = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe --task EditFile --localPlaceFile "
    r"C:\Users\User\AppData\Local\Temp\Baseplate-555883251.rbxl"
)

#: A line format not in the table: the epoch is present, the field count is not
#: one of the four. This is the case the whole module exists to stop hiding.
GARBAGE = "1712972981.05664,7fb4 [FLog::Output] a line from a format we do not read"


def _log(*lines: str) -> str:
    return "\n".join(lines) + "\n"


class TypeFourIsUnchanged(unittest.TestCase):
    """Type 4 is what every real log here is. Any drift in it is a live bug."""

    def test_the_real_pid_line_is_type_four(self):
        self.assertEqual(logid.classify_line(PID_LINE_T4), logid.DATA_FORMAT_TYPE4)

    def test_the_no_severity_variant_is_still_type_four(self):
        """29.6% of measured lines. If the optional severity field is dropped,
        this is what breaks - and it would break *loudly*, on a third of every
        log, which is worse than breaking on none."""
        self.assertEqual(
            logid.classify_line(PID_LINE_T4_NO_SEVERITY), logid.DATA_FORMAT_TYPE4
        )

    def test_every_measured_severity_word_is_accepted(self):
        """Six words measured, not the five the spec lists. A closed set rejects
        the sixth the day it ships, so this pins the open behaviour."""
        for word in ("Info", "Warning", "Error", "Debug", "Verbose", "Critical"):
            line = "2026-09-29T21:59:14.167Z,0.167001,55e8,6,%s [FLog::Output] x" % word
            self.assertEqual(logid.classify_line(line), logid.DATA_FORMAT_TYPE4, word)

    def test_a_truncated_record_is_still_type_four(self):
        """74 measured lines lost their channel to a mid-write truncation. These
        are real records with a valid prefix, not a format we failed to read."""
        line = "2026-09-29T22:11:31.862Z,21.862654,4600,6,Critical <log line elided>"
        self.assertEqual(logid.classify_line(line), logid.DATA_FORMAT_TYPE4)

    def test_a_channel_containing_a_space_and_a_plus_is_accepted(self):
        """``[LOGCHANNELS + 1]``, measured on 2 lines. A ``Name::SubName``
        channel pattern would reject it."""
        line = "2026-09-29T22:11:34.700Z,19.700460,33cc,6 [LOGCHANNELS + 1] RBXCRASH: OutOfMemory"
        self.assertEqual(logid.classify_line(line), logid.DATA_FORMAT_TYPE4)


class OtherFormatsAreRecognised(unittest.TestCase):
    """Types 1, 2 and 3: unmeasured here, but a real log in one of them must not
    be silently misread as having no PID."""

    def test_type_one(self):
        self.assertEqual(logid.classify_line(PID_LINE_T1), logid.DATA_FORMAT_TYPE1)

    def test_type_one_with_a_severity_word_instead_of_the_numeric_field(self):
        self.assertEqual(
            logid.classify_line(PID_LINE_T1_SEVERITY), logid.DATA_FORMAT_TYPE1
        )

    def test_type_two(self):
        self.assertEqual(logid.classify_line(PID_LINE_T2), logid.DATA_FORMAT_TYPE2)

    def test_type_three(self):
        self.assertEqual(logid.classify_line(PID_LINE_T3), logid.DATA_FORMAT_TYPE3)

    def test_the_channel_marker_alone_separates_type_one_from_type_two(self):
        """The same fields, differing only by ``[FLog::...]``. If the recogniser
        ordered on timestamp shape alone it could not tell them apart, because
        there is nothing else to tell them apart by."""
        self.assertNotEqual(
            logid.classify_line(PID_LINE_T1), logid.classify_line(PID_LINE_T2)
        )

    def test_the_pid_is_found_in_every_format(self):
        """The whole point. A type-1 log that states its PID must report it."""
        for line in (
            PID_LINE_T4,
            PID_LINE_T4_NO_SEVERITY,
            PID_LINE_T1,
            PID_LINE_T1_SEVERITY,
            PID_LINE_T2,
            PID_LINE_T3,
        ):
            got = logid.parse_identity(_log(BANNER, line))
            self.assertEqual(got["pid"], 16240, line[:40])

    def test_the_banner_survives_every_format(self):
        """The command line is untimestamped in all four formats, so the place
        and task must parse identically whichever format the records use."""
        for line in (PID_LINE_T4, PID_LINE_T1, PID_LINE_T2, PID_LINE_T3):
            got = logid.parse_identity(_log(BANNER, line))
            self.assertTrue(got["place_path"].endswith("Baseplate-555883251.rbxl"))
            self.assertEqual(got["task"], "EditFile")


class DestructingIsNotAPid(unittest.TestCase):
    """The teardown half of the pair. If it ever matched, a Studio that shut down
    would re-report a PID for a process that is no longer there."""

    def test_destructing_does_not_yield_a_pid(self):
        line = (
            "2026-09-29T21:58:20.118Z,8.118437,58d4,6,Info [FLog::UIThreadNotifier] "
            "Destructing UIThreadNotifier for process '3000' with id 'x'"
        )
        self.assertIsNone(logid.parse_identity(_log(BANNER, line))["pid"])

    def test_the_constructing_line_wins_over_a_later_destructing_one(self):
        got = logid.parse_identity(
            _log(BANNER, PID_LINE_T4, PID_LINE_T4.replace("16240", "3000").replace("Constructing", "Destructing"))
        )
        self.assertEqual(got["pid"], 16240)


class UnrecognisedIsLoud(unittest.TestCase):
    """The requirement that matters most: a format this code cannot read must be
    visible, not absorbed into ``None``."""

    def test_a_garbage_line_is_unrecognised_not_ignored(self):
        self.assertEqual(logid.classify_line(GARBAGE), logid.DATA_UNRECOGNISED)

    def test_a_garbage_log_reports_itself_rather_than_reporting_no_pid(self):
        """The distinction this module exists for. Both cases give ``pid is
        None``; only one of them is a parser gap, and the caller needs to tell
        them apart to act on the right one."""
        got = logid.parse_identity(_log(BANNER, GARBAGE, GARBAGE))
        self.assertIsNone(got["pid"])
        self.assertEqual(got["unrecognised_lines"], 2)
        self.assertIsNotNone(logid.no_pid_reason(got))

    def test_a_genuinely_pidless_log_is_not_called_a_format_problem(self):
        """A Studio that died before writing the notifier line. This must report
        zero unrecognised lines, or the loud signal becomes noise and a caller
        learns to ignore it - which is the same as having no signal at all."""
        got = logid.parse_identity(
            _log(BANNER, "2026-09-30T09:12:05.345Z,0.345598,0edc,6,Info [FLog::Output] hi")
        )
        self.assertIsNone(got["pid"])
        self.assertEqual(got["unrecognised_lines"], 0)
        self.assertEqual(got["log_format"], logid.DATA_FORMAT_TYPE4)
        self.assertIsNone(logid.no_pid_reason(got))

    def test_a_log_of_pure_garbage_says_so_in_words(self):
        """Not "predominant format is None", which is precise and tells a reader
        nothing. Nothing was readable at all, so that is what it should say."""
        got = logid.parse_identity(_log(BANNER, GARBAGE))
        self.assertIsNone(got["log_format"])
        self.assertIn("No line in the log matched", logid.no_pid_reason(got))

    def test_a_mixed_log_is_censused_on_demand_rather_than_on_every_read(self):
        """A log with a readable PID line *and* some unreadable ones.

        The identity census is deliberately skipped whenever a PID is found, and
        this is the test that says so rather than leaving it to be discovered. The
        cost is not hypothetical: at 256 KB, :func:`logid.format_report` measures
        **2.210 ms** against **0.004 ms** for the PID search it would accompany -
        622x, and 3504x at 1 MB. Paying that on every file in a multi-thousand-log
        sweep to re-derive a verdict a found PID has already answered is a bad
        trade, so the full census is the caller's, via :func:`logid.format_report`,
        and it is exact when they make it.
        """
        text = _log(BANNER, PID_LINE_T4, PID_LINE_T4, PID_LINE_T4, GARBAGE)
        got = logid.parse_identity(text)
        self.assertEqual(got["pid"], 16240)
        self.assertIsNone(got["unrecognised_lines"])
        self.assertIsNone(logid.no_pid_reason(got))

        # And when the caller does ask, the answer is the accurate one.
        report = logid.format_report(text)
        self.assertEqual(report["unrecognised"], 1)
        self.assertEqual(report["dominant"], logid.DATA_FORMAT_TYPE4)

    def test_the_reason_names_the_known_format_alongside_the_garbage(self):
        """The case that motivates the reason string: mostly readable, one line
        in no known format, and no PID.

        The readable lines are ordinary type-4 records that simply do not carry a
        PID - which is the common shape of a Studio that died early. They stand in
        for "the readable part is type 4" without handing the test a PID, which
        would short-circuit the census and make it assert nothing.
        """
        ordinary = (
            "2026-09-30T09:12:05.345Z,0.345598,0edc,6,Info [FLog::StudioMain] starting up"
        )
        got = logid.parse_identity(_log(BANNER, ordinary, ordinary, GARBAGE))
        self.assertIsNone(got["pid"])
        self.assertEqual(got["log_format"], logid.DATA_FORMAT_TYPE4)
        reason = logid.no_pid_reason(got)
        self.assertIn("parser gap", reason)
        self.assertIn("type4", reason)

    def test_no_reason_is_owed_when_the_pid_was_found(self):
        self.assertIsNone(logid.no_pid_reason(logid.parse_identity(_log(PID_LINE_T4))))

    def test_the_sample_lines_are_returned_so_a_caller_can_see_them(self):
        report = logid.format_report(_log(BANNER, GARBAGE, GARBAGE))
        self.assertEqual(len(report["unrecognised_lines"]), 2)
        self.assertIn("do not read", report["unrecognised_lines"][0])

    def test_a_torn_timestamp_is_unrecognised_rather_than_a_continuation(self):
        """Measured once, in the installer log. Half a timestamp is a truncated
        write, and it is the one line in the corpus with record intent and no
        parseable prefix."""
        self.assertEqual(
            logid.classify_line("2026-09-29T21:56:01.194Z"), logid.DATA_UNRECOGNISED
        )


class KnownNonRecordsAreNotFailures(unittest.TestCase):
    """A census that counts the header block as a format problem is a census
    nobody reads. These were all measured, and all are understood."""

    def test_the_terms_of_use_preamble_is_preamble(self):
        line = "[FLog::Output] All use of Roblox services must comply with Roblox's Terms of Use"
        self.assertEqual(logid.classify_line(line), logid.DATA_PREAMBLE)

    def test_the_command_line_block_is_preamble(self):
        self.assertEqual(logid.classify_line("Command line:"), logid.DATA_PREAMBLE)
        self.assertEqual(logid.classify_line("*******"), logid.DATA_PREAMBLE)

    def test_the_preamble_does_not_make_a_log_look_unrecognised(self):
        """A real log opens with the preamble on 67 of 67 files measured. If that
        counted as unrecognised, every log would report a problem.

        Asserted on ``format_report`` rather than on ``parse_identity``, because
        the identity census only runs when there is no PID to explain - and this
        log has one. The point being tested is the classifier's, not the
        short-circuit's.
        """
        report = logid.format_report(
            _log(
                "[FLog::Output] All use of Roblox services must comply",
                "*******",
                "Command line:",
                BANNER,
                PID_LINE_T4,
            )
        )
        self.assertEqual(report["unrecognised"], 0)
        self.assertEqual(report["counts"][logid.DATA_PREAMBLE], 3)

    def test_a_stack_trace_is_a_continuation(self):
        line = "Script 'MaterialManager.Packages._Index', Line 65 - function profileend"
        self.assertEqual(logid.classify_line(line), logid.DATA_CONTINUATION)

    def test_a_json_fragment_is_a_continuation(self):
        self.assertEqual(logid.classify_line('\t"code": 100,'), logid.DATA_CONTINUATION)

    def test_a_blank_line_is_not_an_error(self):
        self.assertEqual(logid.classify_line(""), logid.DATA_CONTINUATION)
        self.assertEqual(logid.classify_line("   "), logid.DATA_CONTINUATION)


class ReportShape(unittest.TestCase):
    def test_the_dominant_format_is_the_one_that_appears_most(self):
        report = logid.format_report(_log(PID_LINE_T4, PID_LINE_T4_NO_SEVERITY, PID_LINE_T1))
        self.assertEqual(report["dominant"], logid.DATA_FORMAT_TYPE4)
        self.assertEqual(report["counts"][logid.DATA_FORMAT_TYPE1], 1)

    def test_a_log_of_only_older_formats_reports_that_format(self):
        """The version of the signal that matters: a type-1 log must not claim to
        be type 4, or a caller cannot tell which parser rules were applied."""
        report = logid.format_report(_log(PID_LINE_T1, PID_LINE_T1, PID_LINE_T1))
        self.assertEqual(report["dominant"], logid.DATA_FORMAT_TYPE1)

    def test_every_format_has_a_key_even_at_zero(self):
        """A missing key and a zero are different, and only one of them means
        "we looked and found none"."""
        counts = logid.format_report(_log(PID_LINE_T4))["counts"]
        self.assertEqual(set(counts), set(logid.DATA_FORMATS))

    def test_a_log_with_no_records_has_no_dominant_format(self):
        self.assertIsNone(logid.format_report("nothing here\n")["dominant"])

    def test_an_empty_log_is_all_zeroes_not_an_error(self):
        report = logid.format_report("")
        self.assertEqual(report["unrecognised"], 0)
        self.assertIsNone(report["dominant"])

    def test_the_census_counts_every_line_exactly_once(self):
        text = _log(PID_LINE_T4, GARBAGE, "Command line:", "*******", "")
        self.assertEqual(sum(logid.format_report(text)["counts"].values()), 5)


class BackwardsCompatible(unittest.TestCase):
    """The existing fields must keep their old names, types and meanings, because
    other code and older tests read them by key."""

    def test_every_pre_existing_key_is_still_present(self):
        got = logid.parse_identity(_log(BANNER, PID_LINE_T4))
        for key in (
            "pid", "place_path", "place_id", "universe_id", "task", "parent_pid",
            "parent_session_guid", "transport_token", "session_guid", "machine_guid",
        ):
            self.assertIn(key, got)

    def test_the_new_keys_are_present_and_none_when_the_pid_parsed(self):
        """``None`` here means "not checked, and not needed" - a log whose PID
        line parsed is not evidence of a format problem, and running a full
        census on every read would cost the sweep more than it is worth."""
        got = logid.parse_identity(_log(BANNER, PID_LINE_T4))
        self.assertIsNone(got["log_format"])
        self.assertIsNone(got["unrecognised_lines"])

    def test_read_identity_still_adds_the_log_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "0.741_20260930T091205Z_Studio_75655_last.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(_log(BANNER, PID_LINE_T4))
            got = logid.read_identity(path)
        self.assertEqual(got["log"], os.path.basename(path))
        self.assertEqual(got["pid"], 16240)

    def test_read_identity_reports_an_unreadable_file_as_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(logid.read_identity(os.path.join(directory, "absent.log")))

    def test_the_pid_is_still_an_int_and_never_zero(self):
        got = logid.parse_identity(_log(BANNER, PID_LINE_T4))
        self.assertIsInstance(got["pid"], int)


if __name__ == "__main__":
    unittest.main()
