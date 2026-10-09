"""The ``-parentPid`` edge: reaching a Studio that reports ``name: null``.

A play test's ``StartServer`` and its ``StartClient``s report no place name from
the mesh. That is not a name that is hard to look up, it is the truth: a session
member opens no document of its own, so there is nothing to compare a basename
against and the whole chain ``studio_id -> mesh name -> log -> pid`` stops at its
first step.

What does exist is stated by the child about itself, in its own command line::

    -task StartServer -localProjectFile <path> -parentPid 22656

``-parentPid`` names the launching Studio's process outright. So a child is one
hop from a process whose place is already known, with no console write.

**Provenance of the fixtures below, kept apart because the distinction decides
how far these tests can be trusted.** The banner prefixes - the executable path,
``-placeVersion 0 -creatorId 0``, ``-task StartServer``, ``-localProjectFile`` -
are verbatim from real logs on this machine and are already exercised by
``test_logid.py``. The ``-parentPid`` values are the pids from the recorded
measurement in ``TODO.md`` ("StartServer 19028 with 4 StartClients all parented
to it, and the server itself parented to the Edit Studio that started the test
(22656)"), reassembled onto those banners because the full measured command lines
were not kept. The parse is therefore verified against each half separately and
against their combination; the *flag neighbours* around ``-parentPid`` on a real
play-test banner are not. Nothing here was run against a live Studio, and no
claim about the mesh's behaviour is made beyond what the reasoning below states
outright.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import instance as inst  # noqa: E402
from roblox_studio_mcp.extended import logid  # noqa: E402

EXE = (
    r"C:\Users\User\AppData\Local\Roblox\Versions\version-76e1a02649ad4f35"
    r"\RobloxStudioBeta.exe"
)
PROJECT = r"C:/Users/User/AppData/Local/Temp/robloxstudio-mcp-baseplates/Baseplate-1340472086.rbxl"

#: The Edit Studio that pressed Play. Named, so reachable by the name join, and
#: the root every play-test edge points back to.
EDIT_PID = 22656
#: The server, and the client it spawned. TODO records this shape: one server,
#: several clients, all parented to the server, the server parented to the Edit
#: Studio.
SERVER_PID = 19028
CLIENT_PIDS = (19029, 19030, 19031)

EDIT_SESSION = "1B7B85EC-4838-4EE6-B1D5-FBA1496BC453"
SERVER_SESSION = "4C2A10D7-9E33-4A18-8B0C-77A1E5C9D210"
CLIENT_SESSION = "9D31AA02-6F17-4B44-91C5-3E0B62D48A77"


def edit_banner() -> str:
    return "%s --task EditFile --localPlaceFile %s" % (EXE, PROJECT)


def server_banner(parent: int = EDIT_PID) -> str:
    return (
        "%s -placeVersion 0 -creatorId 0 -task StartServer -localProjectFile %s "
        "-parentPid %d" % (EXE, PROJECT, parent)
    )


def client_banner(parent: int = SERVER_PID) -> str:
    return (
        "%s -task StartClient -rbxTransportToken bG9jYWxfdGVzdA== "
        "-localProjectFile %s -parentPid %d" % (EXE, PROJECT, parent)
    )


def file_new_banner(parent: int = EDIT_PID) -> str:
    """The fourth launch route: File > New. A child, and a *named* one."""
    return (
        "%s -task EditPlace -universeId 0 -placeId 95206881 -userid 1183256136 "
        "-parentPid %d -parentSessionGuid %s -baseUrl https://www.roblox.com "
        "-channel production" % (EXE, parent, EDIT_SESSION)
    )


def launcher_banner(parent: int = 4) -> str:
    """The Roblox launcher process. It carries a ``-parentPid`` and no task."""
    return (
        "%s -startEvent www.roblox.com/robloxQTStudioStartedEvent "
        '-launchIntentString {"task":"None"} -parentPid %d' % (EXE, parent)
    )


def log_text(banner: str, pid: int, session: str = "") -> str:
    """One log, framed the way a real one is."""
    body = (
        "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] "
        "All use of Roblox services must comply with Roblox's Terms of Use\n"
        + banner
        + "\n"
        + "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] "
        "Constructing UIThreadNotifier for process '%d' with id "
        "'https://www.roblox.com-Studio'" % pid
    )
    if session:
        body += (
            "\n2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] "
            "Session GUID is %s" % session
        )
    return body + "\n"


def identity(banner: str, pid: int, session: str = "", log: str = "") -> dict:
    """What ``parse_identity`` makes of a banner, in the shape ``live_identities``
    hands the resolver."""
    got = logid.parse_identity(log_text(banner, pid, session))
    got["log"] = log or ("log-%d" % pid)
    return got


class LogDir:
    """A temp log directory with ``logid.log_dir`` pointed at it.

    Restored before the files are removed, so a failure cannot leave the module
    pointing at a deleted directory for the rest of the session - which reads as
    every later log test finding nothing.
    """

    def __init__(self, logs):
        self._logs = logs
        self._dir = None
        self._restore = None

    def __enter__(self):
        self._dir = tempfile.mkdtemp(prefix="logid-parent-")
        self._restore = logid.log_dir
        logid.log_dir = lambda: self._dir
        for name, text in self._logs.items():
            with open(os.path.join(self._dir, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        return self._dir

    def __exit__(self, *_exc):
        logid.log_dir = self._restore
        for name in os.listdir(self._dir):
            os.remove(os.path.join(self._dir, name))
        os.rmdir(self._dir)
        return False

    @staticmethod
    def name(pid: int) -> str:
        stamp = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
        return "0.741.19.7411056_%s_Studio_%05X_last.log" % (
            stamp.strftime("%Y%m%dT%H%M%SZ"), pid
        )


# --------------------------------------------------------------------------- #
# What the parser already had, and what it must not start doing
# --------------------------------------------------------------------------- #

class ParentFlagParsing(unittest.TestCase):
    """``parent_pid`` and ``parent_session_guid`` were already parsed before this
    work. These pin that they stay parsed, and pin the edges of the pattern -
    the point is not that the flag works, it is that a *nearby* flag cannot be
    mistaken for it."""

    def test_server_and_client_both_state_their_parent(self):
        self.assertEqual(
            logid.parse_identity(log_text(server_banner(), SERVER_PID))["parent_pid"],
            EDIT_PID,
        )
        self.assertEqual(
            logid.parse_identity(log_text(client_banner(), CLIENT_PIDS[0]))["parent_pid"],
            SERVER_PID,
        )

    def test_a_process_with_no_parent_flag_reports_none(self):
        """Not zero: zero is a real pid shape, and a caller would walk to a
        process that does not exist."""
        got = logid.parse_identity(log_text(edit_banner(), EDIT_PID))
        self.assertIsNone(got["parent_pid"])
        self.assertIsNone(got["parent_session_guid"])

    def test_parent_guid_is_read_alongside_the_pid(self):
        """Both flags sit on the same line and neither shadows the other. They
        were added together for the File > New child, so a change that keeps one
        and loses the other would be invisible from the pid side."""
        got = logid.parse_identity(log_text(file_new_banner(), 4444))
        self.assertEqual(got["parent_pid"], EDIT_PID)
        self.assertEqual(got["parent_session_guid"], EDIT_SESSION)

    def test_a_non_numeric_parent_is_absent_not_zero(self):
        got = logid.parse_identity(log_text(
            "%s -task StartServer -localProjectFile %s -parentPid unknown" % (EXE, PROJECT),
            7777,
        ))
        self.assertIsNone(got["parent_pid"])

    def test_the_launcher_is_not_a_playtest_task(self):
        """The launcher carries a ``-parentPid`` and ``{"task":"None"}``. Read as
        a play-test member it would sit in the candidate pool for an unnamed
        Studio while being nothing of the sort."""
        got = logid.parse_identity(log_text(launcher_banner(), 999))
        self.assertEqual(got["parent_pid"], 4)
        self.assertFalse(logid.is_playtest_task(got["task"]))

    def test_task_matching_is_case_insensitive_and_never_raises(self):
        for task, expected in (
            ("StartServer", True), ("startclient", True), ("STARTSERVER", True),
            ("EditPlace", False), ("EditFile", False), ("None", False), (None, False),
        ):
            self.assertEqual(logid.is_playtest_task(task), expected, repr(task))


# --------------------------------------------------------------------------- #
# The tree
# --------------------------------------------------------------------------- #

def full_tree():
    """Edit 22656 -> server 19028 -> three clients, the measured shape."""
    ids = {EDIT_PID: identity(edit_banner(), EDIT_PID, EDIT_SESSION)}
    ids[SERVER_PID] = identity(server_banner(), SERVER_PID, SERVER_SESSION)
    for pid in CLIENT_PIDS:
        ids[pid] = identity(client_banner(), pid, CLIENT_SESSION)
    return ids


class Children(unittest.TestCase):
    def setUp(self):
        self.identities = full_tree()
        self.live = set(self.identities)

    def test_only_session_members_are_children(self):
        """Four processes carry the edit task or none; one is the root. The play
        test's membership is what the task records, and the task is the only
        thing that says so."""
        rows = logid.playtest_children(self.identities, self.live)
        self.assertEqual([row["pid"] for row in rows],
                         sorted([SERVER_PID] + list(CLIENT_PIDS)))

    def test_every_child_is_anchored_to_a_live_parent(self):
        for row in logid.playtest_children(self.identities, self.live):
            self.assertTrue(row["anchored"], row["anchor_note"])
            self.assertIn(row["parent_pid"], self.live)

    def test_the_parent_is_identified_through_the_log_pid_join(self):
        """Nothing new identifies the parent: it is a row in the same identity
        map, which exists because the log stated that process's own PID. So the
        child's place comes from a log line, not from a search."""
        server = next(r for r in logid.playtest_children(self.identities, self.live)
                      if r["pid"] == SERVER_PID)
        self.assertEqual(server["parent_place_path"], PROJECT)
        self.assertEqual(server["parent_task"], "EditFile")
        self.assertEqual(server["parent_log"], "log-%d" % EDIT_PID)

    def test_the_chain_reaches_the_studio_that_pressed_play(self):
        """One hop reaches the server, which is a session with no document of its
        own. Two reach the Edit Studio, which is the only one with a place name -
        so the chain is what turns "some unnamed Studio" into a statement about a
        specific place."""
        client = next(r for r in logid.playtest_children(self.identities, self.live)
                      if r["pid"] == CLIENT_PIDS[0])
        self.assertEqual(client["ancestors"], [SERVER_PID, EDIT_PID])

    def test_a_mutual_parent_claim_terminates(self):
        """Two logs each naming the other as parent would otherwise loop forever,
        and a loop inside a resolver is a hang rather than an error - the tool
        call would sit until the client timeout with no message at all."""
        ids = {
            100: identity(server_banner(200), 100),
            200: identity(client_banner(100), 200),
        }
        self.assertEqual(logid.ancestor_chain(100, ids, {100, 200}), [200])
        self.assertEqual(logid.ancestor_chain(200, ids, {100, 200}), [100])

    def test_a_self_parent_claim_terminates(self):
        ids = {100: identity(server_banner(100), 100)}
        self.assertEqual(logid.ancestor_chain(100, ids, {100}), [])

    def test_a_file_new_child_is_never_a_candidate(self):
        """It has a ``-parentPid`` and it did open a place, so keying on the pid
        edge alone would put a *named* Studio in the pool for an unnamed one -
        exactly the confusion the name join exists to prevent."""
        ids = {EDIT_PID: identity(edit_banner(), EDIT_PID),
               4444: identity(file_new_banner(), 4444)}
        self.assertEqual(logid.playtest_children(ids, set(ids)), [])

    def test_rows_come_back_in_pid_order(self):
        """Determinism is what lets a caller read a reason that names pids."""
        ids = {pid: identity(client_banner(SERVER_PID), pid)
               for pid in (300, 100, 200)}
        self.assertEqual([r["pid"] for r in logid.playtest_children(ids, set(ids))],
                         [100, 200, 300])


# --------------------------------------------------------------------------- #
# Resolving
# --------------------------------------------------------------------------- #

class ResolveUnnamed(unittest.TestCase):
    def setUp(self):
        self.identities = full_tree()
        self.live = set(self.identities)

    def _only_server_tree(self):
        """The Edit Studio and its server, with the client gone: one anchored
        child and one unnamed mesh row."""
        ids = {pid: self.identities[pid] for pid in (EDIT_PID, SERVER_PID)}
        return ids, set(ids)

    def test_the_server_alone_is_resolved(self):
        """The 1:1 case: the Edit Studio is running, one play-test process is
        alive, and the mesh holds one unnamed row. The counts account for each
        other, so the join is forced rather than chosen."""
        ids = {pid: self.identities[pid] for pid in (EDIT_PID, SERVER_PID)}
        got = logid.resolve_unnamed_studio(ids, set(ids), 1)
        self.assertTrue(got["resolved"])
        self.assertEqual(got["child"]["pid"], SERVER_PID)
        self.assertIsNone(got["reason"])

    def test_a_client_alongside_its_server_does_not_resolve(self):
        """The ordinary play test, and the honest limit of the whole idea: a
        server and its client are two processes and two unnamed mesh rows, and no
        log field says which row is which. The mesh row carries only ``id`` and
        ``name`` (checked directly, no hidden field), so there is nothing left to
        read. Refusing here is the point - the alternative is a wrong PID into a
        function whose job is to kill that process."""
        ids = {pid: self.identities[pid] for pid in (EDIT_PID, SERVER_PID, CLIENT_PIDS[0])}
        got = logid.resolve_unnamed_studio(ids, set(ids), 2)
        self.assertFalse(got["resolved"])
        self.assertEqual(got["candidates"], sorted([SERVER_PID, CLIENT_PIDS[0]]))

    def test_the_answer_carries_the_place_it_belongs_to(self):
        """The child has no name, so the only thing worth reporting is which
        document it is running - and that comes from the parent, not from it."""
        ids, live = self._only_server_tree()
        child = logid.resolve_unnamed_studio(ids, live, 1)["child"]
        self.assertEqual(child["parent_place_path"], PROJECT)
        self.assertEqual(child["parent_pid"], EDIT_PID)

    def test_a_server_and_its_clients_do_not_resolve(self):
        """The common play test. Four anchored children, four unnamed rows, and
        4! ways to pair them. Nothing in any log says which row is the server."""
        got = logid.resolve_unnamed_studio(self.identities, self.live, 4)
        self.assertFalse(got["resolved"])
        self.assertIn("4 connected mesh rows", got["reason"])
        self.assertIn("server", got["reason"])
        self.assertEqual(got["candidates"], sorted([SERVER_PID] + list(CLIENT_PIDS)))

    def test_two_children_and_two_rows_still_does_not_resolve(self):
        """Fewer candidates is not a smaller ambiguity. This is the case a
        "pick the only one" shortcut would get wrong, and it is a wrong kill."""
        ids = {pid: self.identities[pid] for pid in (EDIT_PID, SERVER_PID, CLIENT_PIDS[0])}
        got = logid.resolve_unnamed_studio(ids, set(ids), 2)
        self.assertFalse(got["resolved"])
        self.assertIn("2 live play-test processes", got["reason"])

    def test_a_lone_child_among_several_unnamed_rows_does_not_resolve(self):
        """One play-test child, two unnamed mesh rows. The other row belongs to a
        Studio that attached with no place open - a real signature, and one no
        log field distinguishes. Assuming the child is the row asked about would
        be a guess with a PID attached."""
        ids, live = self._only_server_tree()
        got = logid.resolve_unnamed_studio(ids, live, 2)
        self.assertFalse(got["resolved"])
        self.assertIn("exactly one", got["reason"])
        self.assertIn("opened no place", got["reason"])

    def test_no_unnamed_rows_at_all_does_not_resolve(self):
        """A ``name: null`` row cannot exist when no row reports a name, so the
        mesh answer and the question disagree. The count is reported rather than
        quietly ignored, because a silent zero would let a caller read a bug
        upstream as a Studio that cannot be identified."""
        ids, live = self._only_server_tree()
        got = logid.resolve_unnamed_studio(ids, live, 0)
        self.assertFalse(got["resolved"])
        self.assertIn("0 connected rows report no name", got["reason"])
        self.assertIn("disagree", got["reason"])

    def test_unresolved_still_reports_what_the_logs_know(self):
        """An unresolved answer that carries no diagnosis forces the caller to go
        back to square one, which is what made this case feel unfixable."""
        got = logid.resolve_unnamed_studio(self.identities, self.live, 4)
        self.assertEqual(len(got["tree"]), 4)
        self.assertEqual(
            sorted(row["pid"] for row in got["tree"]),
            sorted([SERVER_PID] + list(CLIENT_PIDS)),
        )


# --------------------------------------------------------------------------- #
# Negative cases
# --------------------------------------------------------------------------- #

class UnresolvableEdges(unittest.TestCase):
    def test_no_parent_flag_is_reported_as_such(self):
        ids = {100: {"pid": 100, "task": "StartClient", "parent_pid": None}}
        got = logid.resolve_unnamed_studio(ids, {100}, 1)
        self.assertFalse(got["resolved"])
        self.assertIn("no -parentPid", got["reason"])
        self.assertEqual(got["candidates"], [])

    def test_a_parent_pid_that_is_not_running_is_not_anchored(self):
        """The child names its parent, and the parent is gone. Accepting the edge
        anyway would anchor the child to a PID that now belongs to something
        else, or to nothing."""
        ids = {
            100: identity(client_banner(40000), 100),
            EDIT_PID: identity(edit_banner(), EDIT_PID),
        }
        rows = logid.playtest_children(ids, set(ids))
        self.assertFalse(rows[0]["anchored"])
        self.assertIn("not running", rows[0]["anchor_note"])
        self.assertIn("40000", rows[0]["anchor_note"])
        got = logid.resolve_unnamed_studio(ids, set(ids), 1)
        self.assertFalse(got["resolved"])
        self.assertEqual(got["candidates"], [])

    def test_a_parent_outside_the_candidate_pool_is_not_anchored(self):
        """The pool is the running Studio processes. A ``-parentPid`` naming
        anything outside it - the Windows shell, a launcher already exited - is
        not a Studio this project can place, so the child stays unanchored."""
        ids = {100: identity(client_banner(4), 100)}
        rows = logid.playtest_children(ids, {100})
        self.assertFalse(rows[0]["anchored"])
        self.assertIn("not running", rows[0]["anchor_note"])
        self.assertIsNone(rows[0]["parent_place_path"])

    def test_a_running_parent_with_no_log_is_not_anchored(self):
        """Distinct from a dead parent, and the difference matters: the process
        exists, so a later sweep will find its log and the child will resolve.
        Reporting it as dead would send a caller looking for a corpse."""
        ids = {100: identity(client_banner(200), 100), 200: None}
        rows = logid.playtest_children({100: ids[100]}, {100, 200})
        self.assertFalse(rows[0]["anchored"])
        self.assertIn("no Studio log yet", rows[0]["anchor_note"])

    def test_no_playtest_process_at_all_names_what_is_missing(self):
        got = logid.resolve_unnamed_studio({100: identity(edit_banner(), 100)}, {100}, 1)
        self.assertFalse(got["resolved"])
        self.assertIn("StartServer", got["reason"])
        self.assertIn("StartClient", got["reason"])
        self.assertIn("attached with no place open", got["reason"])

    def test_a_self_parent_is_refused(self):
        ids = {100: identity(server_banner(100), 100)}
        rows = logid.playtest_children(ids, {100})
        self.assertFalse(rows[0]["anchored"])
        self.assertIn("this process", rows[0]["anchor_note"])

    def test_an_empty_pool_resolves_to_nothing_rather_than_everything(self):
        """No logs read at all must be an unresolved answer with a reason, never a
        silent pass that lets a caller treat "no data" as "no conflict"."""
        got = logid.resolve_unnamed_studio({}, set(), 1)
        self.assertFalse(got["resolved"])
        self.assertEqual(got["candidates"], [])
        self.assertIn("no running Studio process", got["reason"])
        self.assertIn("no place name", got["reason"])


# --------------------------------------------------------------------------- #
# The GUID cross-check
# --------------------------------------------------------------------------- #

class GuidCrossCheck(unittest.TestCase):
    """``-parentSessionGuid`` against the parent log's ``Session GUID is``.

    Two records of the same edge, so a disagreement is evidence of something.
    The premise - that the flag holds the parent *process's* session GUID - is
    read off the flag's name and one command line, and was never observed to
    hold. So the check is reported and never obeyed."""

    def _child(self, stated):
        return {
            "pid": 4444, "task": "StartClient", "parent_pid": EDIT_PID,
            "parent_session_guid": stated,
        }

    def test_matching_guids_confirm(self):
        ids = {
            EDIT_PID: {"pid": EDIT_PID, "task": "EditFile", "session_guid": EDIT_SESSION},
            4444: self._child(EDIT_SESSION),
        }
        rows = logid.playtest_children(ids, set(ids))
        self.assertTrue(rows[0]["parent_guid_confirms"])

    def test_mismatched_guids_are_reported_as_false_not_omitted(self):
        """"Checked and disagreed" and "not checked" must not look the same, or
        a caller cannot tell a clean join from an unverified one."""
        ids = {
            EDIT_PID: {"pid": EDIT_PID, "task": "EditFile", "session_guid": SERVER_SESSION},
            4444: self._child(EDIT_SESSION),
        }
        rows = logid.playtest_children(ids, set(ids))
        self.assertIs(rows[0]["parent_guid_confirms"], False)

    def test_comparison_ignores_case(self):
        """"parent_pid" is uppercased at parse and "session_guid" is not, so a
        byte comparison would disagree on a log that spelled its own GUID in
        lower case - a false alarm on a correct edge."""
        ids = {
            EDIT_PID: {"pid": EDIT_PID, "task": "EditFile", "session_guid": EDIT_SESSION.lower()},
            4444: self._child(EDIT_SESSION),
        }
        self.assertTrue(logid.playtest_children(ids, set(ids))[0]["parent_guid_confirms"])

    def test_missing_on_either_side_is_none(self):
        for parent_guid, stated in ((EDIT_SESSION, None), (None, EDIT_SESSION)):
            parent = {"pid": EDIT_PID, "task": "EditFile"}
            if parent_guid:
                parent["session_guid"] = parent_guid
            ids = {EDIT_PID: parent, 4444: self._child(stated)}
            self.assertIsNone(
                logid.playtest_children(ids, set(ids))[0]["parent_guid_confirms"]
            )


# --------------------------------------------------------------------------- #
# End to end through the log files, then through the resolver
# --------------------------------------------------------------------------- #

class FromRealLogFiles(unittest.TestCase):
    """The chain, from a directory of log files: parse -> identity map -> resolve.

    A hand-built identity dict proves the walk; this proves the walk is fed by
    what the sweep actually produces, including the case that broke the first
    draft of any such thing - a log whose PID line has not been written yet.
    """

    def setUp(self):
        self.logs = {
            LogDir.name(EDIT_PID): log_text(edit_banner(), EDIT_PID, EDIT_SESSION),
            LogDir.name(SERVER_PID): log_text(server_banner(), SERVER_PID, SERVER_SESSION),
            LogDir.name(CLIENT_PIDS[0]): log_text(client_banner(), CLIENT_PIDS[0],
                                                  CLIENT_SESSION),
        }

    def test_the_whole_chain_from_files_on_disk(self):
        """parse -> identity map -> parent walk, on real files in a real
        directory rather than on a dict a test wrote."""
        live = [EDIT_PID, SERVER_PID]
        with LogDir(self.logs):
            identities = logid.live_identities(live)
            got = logid.resolve_unnamed_studio(identities, set(live), 1)
        self.assertTrue(got["resolved"])
        self.assertEqual(got["child"]["pid"], SERVER_PID)
        self.assertEqual(got["child"]["parent_pid"], EDIT_PID)
        self.assertEqual(got["child"]["parent_place_path"], PROJECT)
        # The cross-check is unavailable here, not clean. ``-parentSessionGuid``
        # was only ever observed on the File > New child, so a play-test banner
        # carrying it is a fixture this project has not seen. "None" and "True"
        # have to stay distinguishable, so the honest value is asserted.
        self.assertIsNone(got["child"]["parent_guid_confirms"])

    def test_a_live_client_stops_the_join_from_files_too(self):
        """The refusal is a property of the data, not of the test fixture, so it
        has to hold when the identities come off disk rather than out of a dict."""
        live = [EDIT_PID, SERVER_PID, CLIENT_PIDS[0]]
        with LogDir(self.logs):
            identities = logid.live_identities(live)
            got = logid.resolve_unnamed_studio(identities, set(live), 2)
        self.assertFalse(got["resolved"])
        self.assertEqual(got["candidates"], sorted([SERVER_PID, CLIENT_PIDS[0]]))

    def test_a_log_with_no_pid_line_cannot_anchor_anything(self):
        """Measured: a Studio that dies in its first 0.36s writes a 1,335-byte log
        with no notifier line. It has no PID, so the sweep cannot place it - and
        the failure mode to avoid is attributing it to some *other* process, which
        would put a pid that does not exist into a kill."""
        truncated = server_banner()
        live = [EDIT_PID, SERVER_PID, 4242]
        with LogDir(dict(self.logs, **{LogDir.name(4242): truncated})):
            identities = logid.live_identities(live)
            got = logid.resolve_unnamed_studio(identities, set(live), 1)
        self.assertNotIn(4242, identities)
        self.assertEqual(got["candidates"], [SERVER_PID])

    def test_a_start_time_window_that_excludes_everything_costs_time_not_an_identity(self):
        """A window wrong by a day would otherwise drop every child on a machine
        that has been up a while, and the resolver would report "no Studio" for a
        Studio that is running."""
        live = [EDIT_PID, SERVER_PID]
        with LogDir(self.logs):
            stale = {pid: 0.0 for pid in live}
            identities = logid.live_identities(live, stale)
            got = logid.resolve_unnamed_studio(identities, set(live), 1)
        self.assertTrue(got["resolved"])
        self.assertEqual(got["child"]["pid"], SERVER_PID)


def _rows(names):
    """Mesh-shaped rows. The mesh carries only ``id`` and ``name``; checked
    directly, with no hidden field to fall back on."""
    return [{"id": "sid-%d" % i, "name": name} for i, name in enumerate(names)]


class ResolverWiring(unittest.TestCase):
    """``instance.resolve_pid_for_studio`` on a row that reports no name.

    A plain ``TestCase`` running ``asyncio.run``, not
    ``IsolatedAsyncioTestCase``: the resolver is a coroutine, not a test, and
    driving it that way is what the tool does.
    """

    def setUp(self):
        self.identities = full_tree()
        self.processes = [{"pid": pid, "created": "2026-09-30 12:00:00"}
                          for pid in sorted(self.identities)]
        self.client = mock.Mock()
        # A write here would fail the test rather than pollute the user's console.
        self.client.execute_luau = mock.AsyncMock(side_effect=AssertionError(
            "the parent edge must not write to the console"))

    def _only(self, *pids):
        """Narrow the process pool and the identity map to ``pids``."""
        tree = full_tree()
        self.identities = {pid: tree[pid] for pid in pids}
        self.processes = [{"pid": pid, "created": "2026-09-30 12:00:00"} for pid in pids]

    def _run(self, rows, allow_write, studio_id="sid-0"):
        self.client.list_studios = mock.AsyncMock(return_value=rows)
        with mock.patch.object(inst, "list_studio_processes",
                               return_value=self.processes), \
             mock.patch.object(logid, "live_identities",
                               return_value=self.identities):
            return asyncio.run(
                inst.resolve_pid_for_studio(self.client, studio_id, allow_write)
            )

    def test_a_named_row_still_takes_the_name_path(self):
        """The new branch is on ``not name``. A regression that sent every row
        through it would break the case that already worked, which is the worse
        breakage because it is the common one."""
        self._only(EDIT_PID)
        got = self._run(_rows(["Baseplate-1340472086.rbxl"]), False)
        self.assertTrue(got["resolved"])
        self.assertEqual(got["pid"], EDIT_PID)
        self.assertIn("log command line", got["how"])

    def test_a_named_row_matching_a_live_play_test_still_refuses(self):
        """A play test's server and clients open the Edit Studio's own
        ``-localProjectFile``, so the same basename is in four logs and the name
        join cannot pick one of them either. The new branch must not paper over
        that: the row is named, so the name join owns the decision, and it is
        right to say no rather than to reach for the parent edge and answer a
        different question."""
        got = self._run(_rows(["Baseplate-1340472086.rbxl"]), False)
        self.assertFalse(got["resolved"])
        # Five: the Edit Studio plus its server and three clients, all naming the
        # same project file.
        self.assertIn("5 logs report place", got["error"])
        self.assertNotIn("playtest_tree", got)

    def test_one_child_and_one_unnamed_row_resolves_without_a_console_write(self):
        self._only(EDIT_PID, SERVER_PID)
        got = self._run(_rows(["Baseplate-1340472086.rbxl", None]), False, "sid-1")
        self.assertTrue(got["resolved"])
        self.assertEqual(got["pid"], SERVER_PID)
        self.assertEqual(got["parent_pid"], EDIT_PID)
        self.assertIn("-parentPid", got["how"])
        self.client.execute_luau.assert_not_awaited()

    def test_ambiguous_is_reported_with_the_counts_not_guessed(self):
        got = self._run(_rows(["Baseplate-1340472086.rbxl", None, None, None, None]), False,
                        "sid-1")
        self.assertFalse(got["resolved"])
        self.assertTrue(got["needs_console_write"])
        self.assertIn("4 connected mesh rows", got["error"])
        self.assertEqual(got["candidates"], sorted([SERVER_PID] + list(CLIENT_PIDS)))
        self.assertEqual(len(got["playtest_tree"]), 4)

    def test_a_refused_join_does_not_print_without_permission(self):
        got = self._run(_rows([None, None]), False)
        self.assertFalse(got["resolved"])
        self.assertTrue(got["needs_console_write"])
        self.client.execute_luau.assert_not_awaited()

    def test_the_console_fallback_is_narrowed_to_the_children(self):
        """The point of computing the tree even when it cannot decide: the token
        has to choose between four play-test processes rather than between every
        Studio on the machine."""
        token = mock.AsyncMock(return_value={"resolved": False})
        with mock.patch.object(inst, "_resolve_by_console_token", new=token):
            self._run(_rows([None, None, None, None]), True)
        pool = token.await_args.args[2]
        self.assertEqual(sorted(row["pid"] for row in pool),
                         sorted([SERVER_PID] + list(CLIENT_PIDS)))

    def test_the_fallback_pool_is_every_process_when_nothing_anchors(self):
        """Nothing anchors, and the pool is no longer "every process".

        This test's own name and body are the finding, corrected. It used to
        assert the widening: one unnamed row, one live process, and the token
        fallback ran with a pool of *every live process*, because
        ``[] or all_processes`` is ``all_processes``. That pool is what a token
        print is allowed to name, and a token printed into the one Studio the
        caller named could therefore come back attributed to an unrelated pid.

        Nothing about the count changed - only the consequence did. The empty
        answer is now a refusal carrying the resolver's own reason, and the
        token never runs, because a pool of "everything" is not a pool.
        """
        self._only(EDIT_PID)
        token = mock.AsyncMock(return_value={"resolved": False})
        with mock.patch.object(inst, "_resolve_by_console_token", new=token):
            got = self._run(_rows([None]), True)
        token.assert_not_awaited()
        self.assertFalse(got["resolved"], got)
        self.assertIn("reason" if "reason" in got else "error", got)
        # What the logs did establish is still reported, so an unresolved
        # answer stays a diagnosis rather than a shrug.
        self.assertIn("playtest_tree", got)

    def test_nothing_anchored_and_nothing_live_refuses_to_widen(self):
        """Audit A4, the correction to the test above.

        An empty candidate set used to widen the pool to *every live process*,
        because ``[] or all_processes`` is ``all_processes``. The pool is what a
        token print is allowed to name, so widening it is how a token printed
        into the one Studio the caller named came back attributed to an
        unrelated pid - and this is the stop path, so an arbitrary pid is a
        kill target.

        So the empty case refuses, and the refusal is the resolver's own
        "cannot identify" answer rather than a bare error. The live set is
        still reported under ``candidates`` so the caller can see what the
        resolver was choosing between; it is just no longer the pool.
        """
        self._only()
        token = mock.AsyncMock(return_value={"resolved": False})
        with mock.patch.object(inst, "_resolve_by_console_token", new=token):
            got = self._run(_rows([None]), True)
        self.assertFalse(got["resolved"], got)
        token.assert_not_awaited()
        self.assertIn("playtest_tree", got)

    def test_a_guid_disagreement_is_carried_to_the_caller(self):
        """Reported, never obeyed - but silently dropping it would leave a caller
        unable to tell a clean join from an unverified one."""
        self._only(EDIT_PID, SERVER_PID)
        self.identities[SERVER_PID] = dict(self.identities[SERVER_PID],
                                          parent_session_guid=SERVER_SESSION)
        got = self._run(_rows(["Baseplate-1340472086.rbxl", None]), False, "sid-1")
        self.assertTrue(got["resolved"])
        self.assertIn("unverified", got["warning"])
        self.assertIn("reused pid", got["warning"])

    def test_a_clean_join_carries_no_warning(self):
        self._only(EDIT_PID, SERVER_PID)
        got = self._run(_rows(["Baseplate-1340472086.rbxl", None]), False, "sid-1")
        self.assertTrue(got["resolved"])
        self.assertNotIn("warning", got)

    def test_an_unknown_studio_id_is_still_refused_before_any_of_this(self):
        """The parent edge must not turn "no such Studio" into a process. The id
        check is first for that reason."""
        self._only(EDIT_PID, SERVER_PID)
        got = self._run(_rows(["Baseplate-1340472086.rbxl", None]), False, "sid-nope")
        self.assertFalse(got["resolved"])
        self.assertIn("no connected Studio has id", got["error"])


if __name__ == "__main__":
    unittest.main()
