"""Reading a Studio process's identity out of its own log file.

This exists because the obvious join to a process -- print a token, then sweep
every log for it -- turned out to be unnecessary. A Studio log already states
its own PID, so ``log -> pid`` needs no Studio round trip and no console write.
Measured over 66 log files: 64 of 66 carried the line, all 64 PIDs distinct, and
none was reused across two files. The two without are a non-Studio installer log
and a 1,335-byte log from a process that lived 0.36 s; see :func:`no_pid_reason`.

Three fields come out of one read, because reading the log twice to get two
fields is how the two views drift apart:

``pid``
    ``FLog::UIThreadNotifier ... for process '16240'``, about a second into the
    process's life. This is what replaces the start-time comparison.
``place_path`` / ``place_id`` / ``task`` / ``parent_pid``
    The launch command line is reproduced verbatim in an untimestamped header
    line, and it names the place under three different spellings: the Edit task
    logs ``--localPlaceFile``, a play test logs ``-localProjectFile`` for both
    its server and its clients, and a URI launch logs
    ``roblox-studio:1+task:EditPlace+placeId:N+universeId:M``. The task tells the
    role, and ``-parentPid`` links a play test's client to the server that
    started it, so a whole process tree comes out of the logs with no writes.
``session_guid`` / ``machine_guid``
    Written once at startup. The session GUID was distinct in all 45 logs parsed,
    so it is a per-process value; the machine GUID was not (2 distinct values),
    so it is not a dependable host identity and is not used for joining.

What this cannot do
-------------------
It does not identify a ``studio_id``. The mesh names a place and the log records
a command line, and those meet directly for the file route, where the mesh name
is the temp file's basename and the command line contains that basename
verbatim.

The URI route joins as well, in two stages rather than not at all. Its mesh name
is ``Template_<placeId>_AutoRecovery_<N>.rbxl`` and the place id is in the log, so
:func:`match_mesh_name` narrows the candidates by it. Two or more URI launches of
one place are separated by ``N``, read from the path-suffixed ``PlaceSessionId``
line that the command line does not carry --
:func:`_refine_by_autorecovery_counter`. When that read cannot decide, the
caller keeps the full candidate list rather than narrowing to a guess, and
:func:`ambiguous_reason` names the remaining situation instead of guessing.

The one case that is *not* a naming problem is a play test. A ``StartServer`` and
its ``StartClient``s report ``name: null`` because a session member opens no
document of its own, so there is no name to match - not a name that is hard to
look up. The ``-parentPid`` on the child's own command line is the edge that
reaches them; see :func:`resolve_unnamed_studio` for what it does and does not
settle.

Everything here is host-side. Nothing is written into the DataModel, so none of
it can reach the place file, a published place, or a team create.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set

from . import platform

# --------------------------------------------------------------------------- #
# Line format recognition
# --------------------------------------------------------------------------- #
#
# Why this exists. The identity fields below are matched on **message text**, not
# on a line prefix, and that is deliberate: the PID line is identified by
# ``Constructing UIThreadNotifier for process 'N'`` wherever it appears, so it
# survives every prefix change Roblox has ever made. But that robustness has a
# blind spot. When no message matches, ``parse_identity`` returns ``pid: None``,
# and to the caller that is indistinguishable from a Studio that died before
# writing the line. Those are different worlds: one is "this process has no
# identity here", the other is "this log is written in a format I do not read".
# Only one of them is a bug.
#
# So a log's *line format* is now recognised separately, and a caller can be told
# which of those two situations they are in. See :func:`format_report` and
# :func:`no_pid_reason`.
#
# What this section changed about the PID, and what it did not
# -----------------------------------------------------------
# The brief this was built from said the gap was the banner regex's ``(?!\d{4}-)``
# lookahead assuming type 4, and that an older-format log "would be silently read
# as having no PID". **The lookahead is not the cause and the conclusion does not
# follow from it.** Measured against the pre-change parser: type 1, whose lines
# *do* carry a ``[FLog::]`` marker and so match the old banner regex's
# requirements, already yielded ``pid: 16240`` correctly. The formats that
# actually lost the PID were **types 2 and 3**, which carry no channel marker at
# all - and the old ``_PID_RE`` required a literal ``UIThreadNotifier]`` before
# the message, so it could not match them.
#
# So the fix is in ``_PID_RE``, not in the banner regex, and the banner regex is
# left exactly as it was. The lookahead's own comment already said it "is not
# doing the job its shape suggests"; measurement agrees, and the correct response
# to a guard that is merely inert is to leave it and document it, not to rewrite
# it on the theory that it was hiding something.
#
# The four shapes, and where the numbering comes from
# -------------------------------------------------
# The numbers are the community convention from the fastlog-viewer's spec
# (``worships/roblox-fastlog-viewer/docs/fastlog.md``), which the project's
# notes cite. **That document's own author flags the naming as his own invention**
# ("this naming convention isn't standard"), and the document disagrees with
# itself in places: its type-1 heading is ``UnixTime,Identifier,LogLevel
# [FLog::...]`` while its type-1 *example* is
# ``1712972981.05664,7fb4,6 [FLog::Output] ...``, which is three fields, not the
# two the heading names, and matches its type-2 heading. So the numbers are
# reported as that document defines them, and the *shapes* are what this code
# actually matches on - see :data:`FORMAT_TYPE1` and its siblings.
#
#: ``ISO8601Z , TimeSinceStarted , Thread , ThreadId [, Severity] [Channel] msg``
#:
#: The only type seen on this machine, and it is not optional: **129,954 of the
#: 130,623** lines across 67 local logs, dominant in all 67.
#:
#: **The severity field is frequently empty, and that is the single most
#: important thing measured here.** 38,462 of those lines - 29.6% of every line
#: in every log measured - are ``ISO,0.499384,0edc,6 [FLog::ClientSettings] ...``,
#: with the LogLevel field *absent* rather than named. So the **majority** form is
#: the one the spec never mentions, while the spec's own type-4 example is the
#: minority (``2024-04-13T01:10:24.830Z,0.830709,6c08,6 [FLog::Output]``). A
#: recogniser written from the spec alone would call 30% of real lines
#: unrecognised, and since unrecognised is *loud*, it would then cry wolf on
#: every single log it ever reads - which is the same failure as never crying
#: wolf at all. So the severity field is optional here, and the channel marker
#: is optional too (74 measured lines have a valid type-4 prefix and no channel,
#: because the logger truncated them mid-write).
#:
#: Severity is matched as *any token*, never as a list of names. Measured words:
#: Info 71,688, Warning 14,283, Error 2,716, Debug 1,662, Verbose 393, plus
#: ``Critical`` on 74 truncated lines. A closed list would reject the seventh
#: word the day it ships.
DATA_FORMAT_TYPE4 = "type4"

#: ``UnixTime , Thread , ThreadId [, Severity] [Channel] msg``
#:
#: **Zero** occurrences across 67 logs and 130,623 lines, so the shape here is
#: taken from the spec's example, not from a local measurement. Kept because it
#: is the format an *older* Studio would write, and the whole point of this
#: section is that an older-format log must not be silently read as "no PID".
DATA_FORMAT_TYPE1 = "type1"

#: ``UnixTime , Thread , ThreadId [, Severity] msg`` - the same fields as type 1
#: with **no** ``[FLog::]`` channel marker, which is what separates the two in
#: the spec (``1712859371.37087,7b3c,6 Initializing new game``). So the channel
#: marker, not the timestamp, is what tells type 1 from type 2 - see
#: :func:`classify_line`. Zero occurrences measured here, for the same reason as
#: :data:`DATA_FORMAT_TYPE1`.
DATA_FORMAT_TYPE2 = "type2"

#: ``TimeSinceStarted Thread: msg`` - ``0.01454 7dbc: FetchClientSettingsDataBlocking ...``.
#: Space-separated rather than comma-separated, with a **named** thread instead of
#: an id. Zero occurrences measured here.
DATA_FORMAT_TYPE3 = "type3"

#: The untimestamped header block, deliberately **not** treated as a format
#: failure. 197 measured: one Terms-of-Use notice per log (67, always line 0), and
#: a ``*******`` / ``Command line:`` block on 65. The notice has record intent but
#: no prefix; the banner has neither. Both are near-constant across the corpus,
#: which is what justifies naming them rather than reporting them as unknown -
#: see :data:`_PREAMBLE_TERMS_RE` for why position is not used.
DATA_PREAMBLE = "preamble"

#: A line that is not a record at all: blank lines, and messages that ran onto
#: more lines - Luau stack frames (``Script 'Foo', Line 65 - function x``), JSON
#: bodies, the ``---- Error caught by React ----`` banners. 471 measured once
#: blank lines are included, and every one is understood: these are not a format
#: this code fails to read, they are the *absence* of a record. The distinction
#: matters, because the whole point of :data:`DATA_UNRECOGNISED` is that it means
#: something - folding "not a record" into it would make it mean "a log line",
#: which is a category of nearly everything.
DATA_CONTINUATION = "continuation"

#: Record intent, no recognised shape. **This is the loud one**, and it is the
#: whole reason the other six classifications exist as separate answers.
#:
#: A line lands here only if it *looks like* it was trying to be a record - it
#: opens with a timestamp or a ``[Channel]`` tag - and then failed every known
#: prefix shape. A caller finding one is looking at a format not in the table
#: above, and cannot assume any prefix-keyed field it got was read correctly.
#:
#: Measured: **1 line**, in ``RobloxStudioInstaller_0EA05.log``, and it is a torn
#: write - a bare ``2026-09-29T21:56:01.194Z`` with no record attached. Zero
#: across all 66 Studio logs. That is the outcome that makes this worth having:
#: one genuine finding in 130,623 lines, and it is the installer log, which the
#: project's own notes already flag as "not a Studio".
DATA_UNRECOGNISED = "unrecognised"

#: Every classification :func:`classify_line` can return.
DATA_FORMATS = frozenset(
    {
        DATA_FORMAT_TYPE1,
        DATA_FORMAT_TYPE2,
        DATA_FORMAT_TYPE3,
        DATA_FORMAT_TYPE4,
        DATA_PREAMBLE,
        DATA_CONTINUATION,
        DATA_UNRECOGNISED,
    }
)

#: The two timestamp shapes, as *shapes*. These are shape tests, not arithmetic:
#: no value is converted, compared or subtracted, because the timestamp never
#: decides anything in this module. What decides the PID is the message text
#: (:data:`_PID_RE`), and what decides the format is the channel marker and the
#: punctuation of the prefix. Type 4 is ISO; types 1 and 2 are a Unix epoch in
#: seconds. The epoch is bounded at 9-11 digits so that ``0.830208`` - which is
#: the *TimeSinceStarted* of a type-4 line, and the whole of a type-3 line -
#: cannot be mistaken for one.
_ISO_TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z"
_EPOCH_TIMESTAMP = r"\d{9,11}\.\d+"

#: One ``,field`` of the FLog prefix. Deliberately any non-comma, non-space run:
#: the fields observed are a float, a hex thread id, a small integer, and a
#: severity word, and the one thing all four have in common is that none of them
#: contains a comma. Spelling them out separately would encode today's field
#: meanings into the recogniser, and the meaning of field 3 is already ambiguous
#: in the spec (its type-4 example puts a thread number in the slot the prose
#: calls LogLevel).
_FIELD = r"[^,\s]+"

#: ``ISO,f,f,f[,f][ Channel]`` and ``Epoch,f,f[,f][ Channel]``.
#:
#: The field counts differ and that is load-bearing, so the two are spelled out
#: rather than shared: type 4 carries ``TimeSinceStarted,Thread,ThreadId`` (three
#: fields) where the spec's type 1/2 example carries ``Thread,ThreadId`` (two).
#: Writing one pattern for both and varying only the timestamp is what this
#: module first did, and it mis-read 91,185 of the 129,901 real lines on this
#: machine as unrecognised - every type-4 line carrying a severity word, because
#: the extra field was left unmatched. That is the worst way for this code to be
#: wrong: a recogniser that miscounts fields does not fail loudly on the lines
#: it gets wrong, it fails loudly on *everything*, which trains a caller to
#: ignore the one signal this section exists to raise.
#:
#: The channel tag is ``[^]]*`` rather than a ``Name::SubName`` pattern because
#: of ``[LOGCHANNELS + 1]``, measured on 2 lines, which contains a space and a
#: ``+``. It is optional because 74 measured lines are records the logger
#: truncated mid-write, losing the channel and, on 57 of them, the whole message.
#: The channel marker, **captured rather than discarded**, because the one thing
#: that separates type 1 from type 2 is whether it is there. An earlier version
#: compiled it as a non-capturing ``(?:...)?`` and then went looking for the
#: channel with a separate search anchored at the start of the line - which
#: never matches, because the channel sits *after* the prefix fields. Every type-1
#: line was therefore classified as type 2, which is a wrong answer rather than
#: a missing one.
_CHANNEL = r"(\[[^\]]*\])?"

_TYPE4_RE = re.compile(
    r"^%s,%s,%s,%s(?:,%s)?\s+%s"
    % (_ISO_TIMESTAMP, _FIELD, _FIELD, _FIELD, _FIELD, _CHANNEL)
)
_TYPE12_RE = re.compile(
    r"^%s,%s,%s(?:,%s)?\s+%s"
    % (_EPOCH_TIMESTAMP, _FIELD, _FIELD, _FIELD, _CHANNEL)
)
_TYPE3_RE = re.compile(r"^\d+\.\d+\s+\S+:")

#: A leading channel marker with no prefix at all. Present on 67 lines - one per
#: log - as the Terms of Use notice, and it is what makes "record intent" a
#: decidable question for a line with no timestamp.
_LEADING_CHANNEL_RE = re.compile(r"^\[[^\]]*\]")

#: The three header shapes, matched on **content**, never on position. Their
#: position is not stable: the Terms of Use notice is line 0 of every log
#: measured, but the ``*******`` / ``Command line:`` / executable-path block lands
#: a few lines later, and whether that is before or after the first type-4 record
#: varies between files. A rule keyed on "before the first record" would therefore
#: classify the same bytes differently in two logs from the same process.
#:
#: Hard-coding these as *known* is what keeps the unrecognised signal usable: they
#: appear on 67 of 67 files, so if they counted as unknown then every log would
#: report a format problem and a caller would learn to ignore the one signal this
#: section exists to raise.
_PREAMBLE_TERMS_RE = re.compile(r"^\[FLog::Output\] All use of Roblox services")
_PREAMBLE_STARS_RE = re.compile(r"^\*+$")
_PREAMBLE_COMMAND_RE = re.compile(r"^Command line:\s*$")


def classify_line(line: str) -> str:
    """Classify one FLog log line into one of :data:`DATA_FORMATS`.

    Type 1 and type 2 differ **only** in whether the ``[FLog::]`` marker is
    present, because that is the sole difference between them in the spec. So the
    channel marker, not the timestamp, separates them - which is why an epoch
    line with a channel is type 1 and the same line without is type 2.

    A record is recognised on its **prefix shape and channel marker**, never on
    the value of its timestamp. No arithmetic is done on any timestamp anywhere
    in this module, because the field this whole file exists to produce is found
    by its message text, and a timestamp-keyed rule would be a second, weaker,
    silently-failing copy of the same lookup.

    Position plays no part: the header block is recognised by its content, not
    by "before the first record", because the ``Command line:`` block lands
    before the first record in some logs and after it in others, and a
    position-keyed rule would classify identical bytes differently in two files.
    """
    stripped = line.rstrip("\r\n")
    if not stripped.strip():
        return DATA_CONTINUATION

    # Ordered cheapest-and-most-common first, because this runs once per line of
    # every log a census touches: 129,954 of the 130,623 lines measured take the
    # first or second branch below.
    if _TYPE4_RE.match(stripped):
        return DATA_FORMAT_TYPE4

    epoch = _TYPE12_RE.match(stripped)
    if epoch:
        # The captured channel is the only difference between type 1 and type 2.
        return DATA_FORMAT_TYPE1 if epoch.group(1) else DATA_FORMAT_TYPE2

    if _TYPE3_RE.match(stripped):
        return DATA_FORMAT_TYPE3

    # The three header shapes, matched on content. Two patterns, three shapes:
    # the terms notice and the asterisks share one alternation because both are
    # fixed prefixes of a line the engine wrote verbatim at startup.
    if _PREAMBLE_TERMS_RE.match(stripped) or _PREAMBLE_STARS_RE.match(stripped):
        return DATA_PREAMBLE
    if _PREAMBLE_COMMAND_RE.match(stripped):
        return DATA_PREAMBLE

    # Record intent: a timestamp-shaped start with fields that do not form any
    # known prefix, or a channel marker with no prefix at all. Both mean the log
    # is written in a shape not in the table, which is the one thing here that
    # must never be silent.
    if (
        _LEADING_CHANNEL_RE.match(stripped)
        or _ISO_TIMESTAMP_LOOSE_RE.match(stripped)
        or _EPOCH_LOOSE_RE.match(stripped)
    ):
        return DATA_UNRECOGNISED

    return DATA_CONTINUATION


#: Splits out so :func:`classify_line` can ask "does this line *look* timestamped"
#: without also accepting it as a well-formed record. Wider than
#: :data:`_ISO_TIMESTAMP` on purpose: a torn write can leave half a timestamp
#: (``2026-09-29T21:56`` in the installer log), and that is precisely the case
#: worth reporting rather than swallowing.
_ISO_TIMESTAMP_LOOSE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_EPOCH_LOOSE_RE = re.compile(r"^\d{9,11}\.")


#: The four record formats, as opposed to the header/continuation/unknown
#: classifications, which are not formats of a record.
DATA_RECORD_FORMATS = frozenset(
    {DATA_FORMAT_TYPE1, DATA_FORMAT_TYPE2, DATA_FORMAT_TYPE3, DATA_FORMAT_TYPE4}
)


def format_report(text: str) -> Dict[str, Any]:
    """Census of every line in a log, by format, plus the loud verdict.

    Returns a dict with one count key per :data:`DATA_FORMATS` member, plus:

    ``dominant``
        The record format that appears most, or ``None`` if the log holds no
        records at all. Type 4 on all 66 Studio logs measured.
    ``unrecognised_lines``
        A short sample of the unrecognised lines, so the report says *which*
        bytes it did not understand rather than only how many. Empty on every
        real Studio log measured.

    The distinction this exists for: ``unrecognised == 0`` means **"every line in
    this log was read as a format we know"**, and ``unrecognised > 0`` means
    **"part of this log is in a format we do not understand"**. Read alongside
    ``pid is None`` from :func:`parse_identity`, that separates the two situations
    a caller cannot otherwise tell apart - a Studio that died before writing its
    PID line, and a parser that cannot read the log in front of it. They call for
    opposite responses: the first is a dead process, the second needs the parser
    extended. Reporting both as ``pid: None`` is the silent-wrong-answer shape
    this project keeps running into.

    Whole-text, not a prefix: a format problem is not guaranteed to be at the top
    of the file, and a census of a prefix would certify a log it never read the
    rest of. Cheap enough - measured 0.130 ms per 256 KB, the same figure
    :data:`PREFIX_BYTES` is sized on.
    """
    counts: Dict[str, int] = {name: 0 for name in sorted(DATA_FORMATS)}
    samples: List[str] = []

    for line in text.splitlines():
        kind = classify_line(line)
        counts[kind] = counts.get(kind, 0) + 1
        if kind == DATA_UNRECOGNISED and len(samples) < 5 and line.strip():
            samples.append(line[:200])

    dominant = None
    record_counts = {name: counts[name] for name in DATA_RECORD_FORMATS}
    if any(record_counts.values()):
        dominant = max(sorted(record_counts), key=lambda name: record_counts[name])

    return {
        "counts": counts,
        "dominant": dominant,
        "unrecognised": counts[DATA_UNRECOGNISED],
        "unrecognised_lines": samples,
    }


#: The PID, as the log states it. Anchored on ``process '`` with digits only, so a
#: longer process name cannot be truncated into a plausible-looking PID.
#:
#: **The channel marker is deliberately absent from this pattern, and that is a
#: fix rather than a loosening.** It used to require a literal ``UIThreadNotifier]``
#: ahead of the message, on the assumption that every FLog line carries an
#: ``[FLog::...]`` tag. That is true of type 4 and false of **types 2 and 3**,
#: which carry no channel marker at all (``0.01454 7dbc: Constructing
#: UIThreadNotifier for process '16240'``). So a type-2 or type-3 log - precisely
#: the older formats this change exists to survive - was read as having **no
#: PID**, with no error, and the process dropped from the join. Measured against
#: the pre-change pattern: type 1 found 16240, type 2 and type 3 both found
#: ``None``.
#:
#: The remaining guards are the verb, the class name and the quoted digits, which
#: together are specific enough: re-run over all 67 logs on this machine, the
#: loosened pattern yields the **same 64 PIDs** as the anchored one, with no
#: false positive. The line that must never match is the teardown half,
#: ``Destructing UIThreadNotifier for process 'N'``, and it cannot: ``Destructing``
#: does not contain ``Constructing``.
_PID_RE = re.compile(r"Constructing UIThreadNotifier for process '(\d+)'")

_SESSION_GUID_RE = re.compile(r"Session GUID is ([0-9A-Fa-f-]{36})")
_MACHINE_GUID_RE = re.compile(r"Machine GUID is ([0-9A-Fa-f-]{36})")

#: The command line, reproduced verbatim. Untimestamped, so it is matched as a
#: whole line rather than through a log-line prefix. The trailing group may be
#: empty, because a manually launched Studio logs the bare executable path.
#:
#: Lives in :mod:`platform` because the binary name differs: a Windows banner ends
#: ``RobloxStudioBeta.exe`` and a macOS one ends ``RobloxStudio``. Hard-coding the
#: suffix here meant a Mac banner matched nothing, and "matched nothing" surfaces
#: as **"no identity", with no error** - the exact failure shape this project has
#: been repeatedly bitten by. Verified here against both spellings.
_CMDLINE_RE = platform.BANNER_RE

#: Place identity, per launch route. Three spellings, all measured:
#:
#: ``--localPlaceFile``  the Edit task, this module's file route
#: ``-localProjectFile`` StartServer and StartClient, i.e. a play test
#: ``roblox-studio:...``  the URI route, with the ids inline
#:
#: Missing the middle one is why a play test's server and clients looked
#: unidentifiable, and the task it names is also how the role is known.
#:
#: These two used to be ``(--localPlaceFile|-localProjectFile)\s+(\S+)``, and that
#: is wrong on macOS. The macOS AutoSaves directory Roblox documents is
#: ``~/Library/Application Support/Roblox/RobloxStudio/AutoSaves`` - the space in
#: ``Application Support`` is not incidental, it is a fixed component of the path.
#: ``(\S+)`` stopped at it, so a Mac launch parsed as
#: ``place_path='/Users/me/Library/Application'``, which is a path that exists
#: nowhere. The failure was silent: a truncated path still produced a basename, so
#: the name-based join went looking for a document called ``Application`` and
#: simply never matched. Found by running the parser against a macOS-shaped
#: banner; Windows cannot produce this input.
_PLACE_PATH_FLAGS = ("--localPlaceFile", "-localProjectFile")

#: A following ``-flag`` token, used to find where a path argument ends. ``/`` and
#: ``\\`` are deliberately absent from the class: a Windows path may well start
#: ``-`` after a drive letter is stripped, and a path is far more likely to contain
#: a slash than to begin with a dash.
_NEXT_FLAG_RE = re.compile(r"\s-{1,2}[A-Za-z]")


def _path_after(args: str, flag: str) -> Optional[str]:
    """The path argument following ``flag``, spaces and all.

    Three rules, in order, because a command line gives no single answer:

    1. **Quoted** - take everything up to the closing quote. Unambiguous.
    2. **Another flag follows** - cut at that flag. Safe, because a real path does
       not contain `` -word``.
    3. **Nothing follows** - take the whole remainder. This is the macOS case: the
       path is last on the line and contains a space, so rule 1 does not apply and
       rule 2 must not fire on a space that is *inside* the value.
    """
    index = args.find(flag)
    if index < 0:
        return None
    rest = args[index + len(flag):].lstrip()
    if not rest:
        return None
    if rest.startswith('"'):
        closing = rest.find('"', 1)
        if closing > 0:
            return rest[1:closing]
        return rest[1:]
    following = _NEXT_FLAG_RE.search(rest)
    return rest[:following.start()] if following else rest
#: The URI route, with the universe id that actually fetches. ``universeId:0`` is
#: what File > New does and what this project now sends; the id is still required
#: as a key, because dropping it left the Studio with no place open.
_URI_PLACE_RE = re.compile(r"\+placeId:(\d+)\+universeId:(\d+)")

#: The place as **separate flags**, which is what a child Studio launched by
#: File > New gets:
#:
#:     -task EditPlace -universeId 0 -placeId 95206881 -userid 1183256136 \
#:       -parentPid 9352 -parentSessionGuid 1B7B85EC-...
#:
#: Missing this form reports ``place_id: None`` for a process that plainly has a
#: place, because ``_URI_PLACE_RE`` only matches the inline ``+placeId:N`` shape.
#: The ``-parentPid`` in the same line is the cleanest identity edge available:
#: it names the launching Studio's process outright, so a child needs no name
#: matching at all.
_FLAG_PLACE_RE = re.compile(r"-placeId\s+(\d+)")
_FLAG_UNIVERSE_RE = re.compile(r"-universeId\s+(\d+)")
_FLAG_PARENT_GUID_RE = re.compile(r"-parentSessionGuid\s+([0-9A-Fa-f-]{36})")
_TASK_RE = re.compile(r"-{1,2}task\s+(\S+)")
_URI_TASK_RE = re.compile(r"\+task:(\S+?)\+")
_INTENT_TASK_RE = re.compile(r'-launchIntentString\s+\{.*?"task"\s*:\s*"(\w+)"')
_PARENT_PID_RE = re.compile(r"-parentPid\s+(\d+)")
_TRANSPORT_RE = re.compile(r"-rbxTransportToken\s+(\S+)")

#: The mesh's name for a URI launch. ``N`` is a per-launch counter, present in the
#: mesh and absent from the log, which is exactly why that route cannot join.
_MESH_URI_NAME_RE = re.compile(r"^Template_(\d+)_AutoRecovery_(\d+)\.rbxl$")

#: Filename stamp, e.g. ``0.741.19.7411056_20260930T091820Z_Studio_D4ED5_last.log``.
LOG_STAMP_RE = re.compile(r"_(\d{8}T\d{6})Z_")

#: ``[telemetryLog] PlaceSessionId: <guid>-<suffix>`` where the suffix is either
#: the numeric place id or, when Studio opened an autorecovery copy, the full path
#: to it. Two shapes, one line, and the shape is the whole point:
#:
#: * ``PlaceSessionId: A3A73B66-0B94-4C5E-AA26-5F44551274BC-95206881``
#: * ``PlaceSessionId: 77C06B69-0B74-4C0B-B082-39E5D0EC16BB-C:/Users/.../AutoSaves\Template_95206881_AutoRecovery_3.rbxl``
#:
#: **The second form is the only record of a URI launch's ``AutoRecovery_N``
#: counter**, which is the one thing that separates two concurrent URI launches of
#: the same place - the case this project recorded as unresolvable. It appears
#: only in the 1 of 16 URI logs where Studio created an autorecovery document at
#: all; the rest opened a plain unsaved "Place1" and have no path to report.
#:
#: The GUID itself is per place-open and distinct in 13 of 13 logs that opened a
#: place, but the mesh never reports it, so it cannot close the join on its own.
_GUID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_PLACE_SESSION_RE = re.compile(r"PlaceSessionId:\s*(%s)-(.*)$" % _GUID, re.MULTILINE)
_DM_ID_RE = re.compile(r"DmId:\s*(%s)-" % _GUID)


def session_guids(text: str) -> List[str]:
    """Every per-place-open GUID in a log, in order, de-duplicated.

    A log can carry more than one: measured, one had 2 GUIDs across 29
    ``PlaceSessionId`` lines, because the numeric and path forms are emitted by
    different subsystems. Both are returned rather than picking the first, since
    which one appears first is not stable enough to depend on.
    """
    seen: List[str] = []
    for match in _PLACE_SESSION_RE.finditer(text):
        guid = match.group(1).upper()
        if guid not in seen:
            seen.append(guid)
    for match in _DM_ID_RE.finditer(text):
        guid = match.group(1).upper()
        if guid not in seen:
            seen.append(guid)
    return seen


def place_session_path(text: str) -> Optional[str]:
    """The place path a log's ``PlaceSessionId`` names, if it named one.

    ``None`` for the numeric-suffix form, which names a published place rather
    than a file. This is the field that carries a URI launch's
    ``AutoRecovery_N`` counter, and therefore the field that tells two concurrent
    URI launches of one place apart.

    Needs the **whole** log: these lines were measured at 282,739 bytes in an
    868 KB file, past any prefix worth reading on every file. Call it only on
    candidates, never in a sweep.
    """
    for match in _PLACE_SESSION_RE.finditer(text):
        tail = match.group(2).strip()
        if ".rbxl" in tail:
            return tail
    return None


def read_place_session_path(path: str) -> Optional[str]:
    """Read one log's place-session path, or ``None`` if unreadable or absent.

    Whole-file read, deliberately. See :func:`place_session_path`.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return place_session_path(handle.read())
    except OSError:
        return None


def log_dir() -> str:
    """Where Studio writes its logs. See :func:`platform.log_dir`."""
    return platform.log_dir()


def parse_identity(text: str) -> Dict[str, Any]:
    """Pull every identity field out of one log's text.

    Returns ``pid``, ``place_path``, ``place_id``, ``universe_id``, ``task``,
    ``parent_pid``, ``transport_token``, ``session_guid``, ``machine_guid``,
    ``log_format`` and ``unrecognised_lines``. Absent fields are ``None`` rather
    than omitted, so a caller can tell "the log does not say" from "not parsed
    yet".

    The last two answer "is this log in a format I understand?", and they are
    populated **only when ``pid`` is ``None``**.

    A log whose PID line parsed is one this module demonstrably *can* read, so
    the census has nothing left to explain. The cost of running it anyway is not
    hypothetical: measured over the local corpus, :func:`format_report` costs
    **2.210 ms** per 256 KB against **0.004 ms** for the PID search it would
    accompany - **622x**, rising to **3504x** at 1 MB, because the census is
    per-line and the search is one pass of C. A whole-file sweep of 67 logs
    (17.7 MB) costs **144 ms** of census, which is fine once and ruinous on every
    pass of a sweep that walks thousands of files.

    So ``None`` there means "not checked, and not needed", deliberately distinct
    from ``0`` meaning "checked, and every line was recognised" - collapsing
    those two is this file's favourite failure mode wearing a different hat. A
    caller who wants the census regardless of the PID calls
    :func:`format_report` directly, which is exact.
    """
    found: Dict[str, Any] = {
        "pid": None,
        "place_path": None,
        "place_id": None,
        "universe_id": None,
        "task": None,
        "parent_pid": None,
        "parent_session_guid": None,
        "transport_token": None,
        "session_guid": None,
        "machine_guid": None,
        # The format verdict. Filled in below only when there is no PID, and
        # left None otherwise. See the docstring.
        "log_format": None,
        "unrecognised_lines": None,
    }

    pid = _PID_RE.search(text)
    if pid:
        found["pid"] = int(pid.group(1))
    else:
        # No PID line anywhere in the text. That is either a Studio that died
        # before writing one, or a log whose format this module cannot read -
        # and the two demand opposite responses, so the log is censused rather
        # than reported as a bare None.
        report = format_report(text)
        found["log_format"] = report["dominant"]
        found["unrecognised_lines"] = report["unrecognised"]

    session = _SESSION_GUID_RE.search(text)
    if session:
        found["session_guid"] = session.group(1)

    machine = _MACHINE_GUID_RE.search(text)
    if machine:
        found["machine_guid"] = machine.group(1)

    # The banner is the untimestamped line holding the executable path. The
    # negative lookahead for a date in :data:`_CMDLINE_RE` keeps ordinary type-4
    # log lines out.
    #
    # That lookahead was long blamed for older-format logs losing their PID. It is
    # not the cause, and the measurement is worth keeping next to the code: a
    # type-1 line starts with an epoch, so the lookahead never excluded it, and
    # the binary-name requirement that does the real work rejected it correctly.
    # The formats that genuinely lost the PID were 2 and 3, for a different reason
    # entirely - see :data:`_PID_RE`. The lookahead is inert rather than harmful,
    # so it stays as it is.
    banner = _CMDLINE_RE.search(text)
    if not banner:
        return found

    args = banner.group(1) or ""

    for flag in _PLACE_PATH_FLAGS:
        place = _path_after(args, flag)
        if place:
            found["place_path"] = place
            break

    uri = _URI_PLACE_RE.search(args)
    if uri:
        found["place_id"] = int(uri.group(1))
        found["universe_id"] = int(uri.group(2))
    else:
        # Separate `-placeId N` / `-universeId M` flags, the File > New form.
        flag_place = _FLAG_PLACE_RE.search(args)
        if flag_place:
            found["place_id"] = int(flag_place.group(1))
        flag_universe = _FLAG_UNIVERSE_RE.search(args)
        if flag_universe:
            found["universe_id"] = int(flag_universe.group(1))

    parent_guid = _FLAG_PARENT_GUID_RE.search(args)
    if parent_guid:
        found["parent_session_guid"] = parent_guid.group(1).upper()

    # Task from the flags, then the URI's own ``+task:``, then the launcher's
    # intent JSON. Any one of the three may be the only one present.
    for pattern in (_TASK_RE, _URI_TASK_RE, _INTENT_TASK_RE):
        task = pattern.search(args)
        if task:
            found["task"] = task.group(1)
            break

    parent = _PARENT_PID_RE.search(args)
    if parent:
        found["parent_pid"] = int(parent.group(1))

    transport = _TRANSPORT_RE.search(args)
    if transport:
        found["transport_token"] = transport.group(1)
    return found


#: How much of a log to read before giving up and reading the rest.
#:
#: The identity lines sit at the very top - command line at byte 507, the two
#: GUIDs at ~3.3 KB, the PID at ~3.7 KB, the largest of 43 logs being 3,809 bytes
#: in a 1.7 MB file. The ``PlaceSessionId`` lines are much deeper: measured at
#: 64,520 to 74,960 bytes across 13 logs, so a 64 KB prefix missed **every one of
#: them**. 256 KB is the measured knee - per-file cost is 0.100 ms at 64 KB and
#: 0.130 ms at 256 KB, a 1.3x cost for 4x the bytes, because the cost is mostly
#: the open rather than the read. Past that it stops being cheap: 1 MB costs
#: 0.626 ms, 6.3x for 16x the bytes.
#:
#: What still does *not* fit is the path-suffixed ``PlaceSessionId`` at 282,739
#: bytes in an 868 KB log, which is the only record of a URI launch's
#: ``AutoRecovery_N`` counter. That is read on demand by
#: :func:`place_session_path` rather than by widening this, because only a handful
#: of logs ever need it.
#:
#: The margin is what makes this safe to *try*, not what makes it correct: a log
#: still being written may not have reached its PID line yet, so a prefix with no
#: PID falls back to a full read rather than reporting a process as unidentified.
PREFIX_BYTES = 256 * 1024

#: Ceiling on the full-file fallback in :func:`read_identity`, in bytes.
#:
#: The fallback exists for one case: a log still being written, whose PID line
#: has not landed yet, must not be reported as an unidentified process. That is
#: a *freshness* race, and it is decided in the first few KB — the largest of 43
#: measured logs is 3,809 bytes in a 1.7 MB file.
#:
#: So the ceiling is sized on the corpus rather than on the big end: 4 MiB is
#: ~2.4x the largest log measured here, and an order of magnitude past any log
#: a Studio is expected to produce. Reading without a limit instead meant one
#: hostile or corrupt file — user-writable, and read on every sweep — could pull
#: an arbitrary number of bytes into memory.
#:
#: When the ceiling bites, the read is *marked* rather than silent:
#: ``read_truncated: True`` rides along with the identity, so "we stopped
#: reading" cannot be mistaken for "the PID is not in this log". A silent cap
#: here would convert a bounded read into a plausible wrong answer, which is the
#: failure class this project keeps paying for.
FULL_READ_BYTES = 4 * 1024 * 1024


def read_identity(path: str) -> Optional[Dict[str, Any]]:
    """Read one log file's identity, or ``None`` if it is unreadable.

    Reads a prefix, and only escalates to the whole file — bounded by
    :data:`FULL_READ_BYTES` — when the prefix has no PID. Everything needed is
    within 4 KB in practice, but "in practice" is not a guarantee for a log that
    is still being written, and silently reporting no PID for a live process
    would drop it from the join entirely.

    The returned identity carries ``read_truncated`` (``False`` in the ordinary
    case), so a caller can tell a capped read from a log that simply has no PID
    further down. See :data:`FULL_READ_BYTES` for why that distinction is
    load-bearing.

    ``None`` on failure is deliberate: a log that is locked, mid-rotation, or
    unreadable is a normal condition, and a resolver that raised here would fail
    the whole call over one file.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read(PREFIX_BYTES)
            truncated = False
            if _PID_RE.search(text) is None:
                # Read one byte past the cap so "there was more" is observable
                # rather than inferred, then drop it.
                rest = handle.read(FULL_READ_BYTES + 1)
                truncated = len(rest) > FULL_READ_BYTES
                if truncated:
                    rest = rest[:FULL_READ_BYTES]
                if rest:
                    text += rest
    except OSError:
        return None

    identity = parse_identity(text)
    identity["log"] = platform.basename(path)
    identity["read_truncated"] = truncated
    return identity


def no_pid_reason(identity: Dict[str, Any]) -> Optional[str]:
    """Why a log's PID could not be read, or ``None`` if it was.

    The counterpart to :func:`ambiguous_reason`, for the other half of the join.
    A caller that lands on ``pid is None`` needs to know which of two very
    different situations it is in before deciding what to do:

    * **the log is fine and simply has no PID** - a Studio that died before
      writing the notifier line. Measured twice on this machine: the installer
      log, and a 1,335-byte Studio log from a process that lived 0.36 s. Nothing
      is wrong and the right answer is "this process is gone".
    * **the log is in a format this module does not read** - a parser gap, which
      needs extending, and which would otherwise be indistinguishable from the
      first case and quietly read as "this process is gone" forever.

    Returns a sentence, because a caller that has to reconstruct this itself
    from a bare integer is a caller that will get it wrong. ``None`` when the
    PID *was* read, or when the format is not the reason - the caller has a PID
    to work with and no explanation is owed.
    """
    if identity.get("pid") is not None:
        return None
    unknown = identity.get("unrecognised_lines")
    if not unknown:
        # Either the format checked out (so the log is well-formed and simply
        # carries no PID line), or no census ran. Both mean "not a format
        # problem", which is the half of the answer that is reassuring.
        return None
    dominant = identity.get("log_format")
    log = identity.get("log")
    where = " in %s" % log if log else ""
    if dominant is None:
        # No record in any known format anywhere in the log. Saying
        # "predominant format is None" would be precise and useless, so name
        # what that actually means: nothing here was readable at all.
        shape = "No line in the log matched any of the four known record formats"
    else:
        shape = "The log's predominant record format is %r" % (dominant,)
    return (
        "%d line(s)%s are in an FLog format this parser does not recognise, so no "
        "PID line could be read from them. %s. This is a parser gap, not a missing "
        "PID" % (unknown, where, shape)
    )


#: How far apart a log's filename stamp and its process's creation time may be and
#: still count as the same process.
#:
#: Measured over 47 real logs: the worst gap between a filename's stamp and that
#: log's own first timestamped line is 1.9s. 60s is a ~30x margin, and the cost of
#: being generous is only that a few more files are read.
#:
#: This is deliberately *not* how a process is identified. It narrows which logs
#: are worth reading; the PID inside the log is what decides. A window that is too
#: narrow costs a slow fallback, never a wrong answer.
START_WINDOW_SECONDS = 60.0

#: PowerShell's ``ConvertTo-Json`` renders CIM datetimes as ``/Date(1790729920871)/``,
#: a millisecond epoch. Every other guess at the format failed on it, and when this
#: parse failed silently the start-time filter skipped every file.
_CIM_JSON_DATE = re.compile(r"/Date\((-?\d+)\)/")


def parse_process_started(value: Any) -> Optional[float]:
    """Epoch seconds from a process creation time, or ``None`` if unparseable.

    Handles the ``/Date(ms)/`` form that ``Get-CimInstance | ConvertTo-Json``
    actually produces, plus the plain ISO and US-shaped strings, because a caller
    that only has one of them should not have to know which.
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None

    match = _CIM_JSON_DATE.search(text)
    if match:
        return int(match.group(1)) / 1000.0

    import datetime as _dt

    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return _dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        return _dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Why a place did not open
# --------------------------------------------------------------------------- #

#: The place-open state machine, in the ``[telemetryLog]`` channel. Measured over
#: 47 logs: 4 ended in ``OpenPlaceFailure``, and every one gave a reason.
#:
#: 3 of the 4 said "Error fetching latest place version", and all 3 were URI
#: launches. That is the cause of the signature this project had been recording as
#: an unexplained "failed launch" - a Studio that attaches to the mesh but never
#: gets a place name. It is a fetch of the published place failing, not a mesh or
#: transport fault, and no amount of retrying the transport fixes it.
#:
#: The 4th said "Connection error 279" and was a play test's StartClient.
_STATE_RE = re.compile(r"\[telemetryLog\]\s*State:\s*(\w+)")
_ERROR_TYPE_RE = re.compile(r"\[telemetryLog\]\s*ErrorType:\s*(\S+)")
_ERROR_MSG_RE = re.compile(r"\[telemetryLog\]\s*ErrorMessage:\s*(.*\S)")
_SIGNED_IN_RE = re.compile(r"\[FLog::LoginController\] Login got Standalone DM ready")
_INSTANCES_AT_LAUNCH_RE = re.compile(r"Running instance count at launch (\d+)")

#: The states that *end* an attempt, as opposed to the ones that report progress.
#:
#: The full sequence measured over 21 successful Edit launches:
#: ``OpenPlaceInitialization -> OpenPlaceCreateDataModel -> OpenPlaceLoadDataModel
#: -> OpenPlaceWaitForStreaming -> OpenPlacePostLoadDataModel
#: -> OpenPlaceEnterDataModelScope -> OpenPlacePreSuccess -> OpenPlaceSuccess``
#: and then it keeps going, to ``PlaceIdle``.
#:
#: Two traps in that list, both hit here:
#:
#: * taking the **last** state reports ``PlaceIdle``, so 21 successful launches
#:   looked like 0 opened. Only the terminal states decide the outcome.
#: * ``OpenPlacePreSuccess`` **ends with "Success"** and is a progress state. A
#:   suffix test would call a half-finished load a success.
#:
#: So the terminal set is spelled out rather than pattern-matched.
_TERMINAL_SUCCESS = frozenset({"OpenPlaceSuccess", "PlaySoloSuccess"})


def open_outcome(text: str) -> Dict[str, Any]:
    """How a Studio's attempt to open a place ended.

    Returns ``state``, ``error_type``, ``error_message``, ``signed_in``,
    ``instances_at_launch`` and ``opened``.

    Only the terminal states move the outcome, so a later success clears an
    earlier failure: Studio retries, and reporting the first failure would blame a
    launch that recovered. A failure's ``ErrorMessage`` arrives on the lines after
    it, so the parser waits for exactly one message rather than grabbing the last
    one in the file.

    ``Hang In Progress`` is deliberately not treated as a hang. It appears in logs
    that went on to open their place normally, including one with 5 instances
    already running. Only ``Hang Detected`` means the hang escalated, and that
    string appears in none of the 47 logs measured here - so on this machine, at
    Studio 0.741, a startup hang is not a thing that happened.
    """
    state = None
    error_type = None
    error_message = None
    signed_in = bool(_SIGNED_IN_RE.search(text))
    instances = None
    count_match = _INSTANCES_AT_LAUNCH_RE.search(text)
    if count_match:
        try:
            instances = int(count_match.group(1))
        except ValueError:
            instances = None

    awaiting_message = False
    for line in text.splitlines():
        found = _STATE_RE.search(line)
        if found:
            candidate = found.group(1)
            terminal = candidate in _TERMINAL_SUCCESS or candidate.endswith("Failure")
            if not terminal:
                continue  # a progress state says nothing about the outcome
            state = candidate
            awaiting_message = candidate.endswith("Failure")
            if not awaiting_message:
                error_type = None
                error_message = None
            continue
        error = _ERROR_TYPE_RE.search(line)
        if error:
            error_type = error.group(1)
            continue
        if awaiting_message:
            message = _ERROR_MSG_RE.search(line)
            if message:
                error_message = message.group(1)
                awaiting_message = False

    return {
        "state": state,
        "error_type": error_type,
        "error_message": error_message,
        "signed_in": signed_in,
        "instances_at_launch": instances,
        "opened": state in _TERMINAL_SUCCESS,
    }


def read_open_outcome(path: str) -> Optional[Dict[str, Any]]:
    """Read one log's place-open outcome, or ``None`` if unreadable.

    This reads the **whole** file, unlike :func:`read_identity`. The identity
    lines sit in the first 4 KB, but the outcome lines appear whenever the load
    finished, which for a slow place is deep into the log. So the prefix that makes
    the identity sweep cheap would silently report "no outcome" here, and the
    outcome is the whole point of this function. Kept off the hot path for that
    reason: only a failure diagnosis pays for it.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return None
    outcome = open_outcome(text)
    outcome["log"] = platform.basename(path)
    return outcome


def stamp_seconds(name: str) -> Optional[float]:
    """Epoch seconds of a log filename's UTC stamp, or ``None``.

    The ``Z`` in the filename is load-bearing, so it is parsed as UTC. Naive
    ``strptime(...).timestamp()`` reads it as local time, which on this machine
    (UTC+3) was three hours early -- and the field this feeds, a process start
    time, is exactly where a three-hour error hides rather than shows.
    """
    match = LOG_STAMP_RE.search(name)
    if not match:
        return None
    try:
        import datetime as _dt

        moment = _dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        return moment.replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def live_identities(
    pids: List[int], started: Optional[Dict[int, float]] = None
) -> Dict[int, Dict[str, Any]]:
    """Map each live PID to its log's identity.

    Scans the log directory and keeps the entries whose stated PID is one of
    ``pids``. This is the step that needs no console write: the log names the
    process, so nothing has to be injected to find out which log is whose.

    ``started`` maps a PID to its creation time in epoch seconds, and narrows which
    files are read to those within :data:`START_WINDOW_SECONDS` of it.

    **The filter is not a clear win, and it was nearly cut for that reason.**
    Measured at 3,000 logs with 3 Studios:

    * live Studios are the newest, the normal case: filter **0.098s**, no filter
      **0.056s**. The filter loses, because stamp ordering already puts the live
      logs first and the read loop stops as soon as it has them all.
    * a live Studio is old and 3,000 newer logs from dead processes follow it:
      filter **0.097s**, no filter **1.285s**. The filter wins by 13x, because
      without it the read loop has to walk every newer log first.

    So it is kept for the second case and tolerated as slight overhead in the
    first. The window only chooses which files to *read*: the PID inside the log
    is still what decides, and a narrowed pass that finds nothing falls back to
    the full directory, so a wrong window costs time rather than a missing
    identity.
    """
    wanted = {int(p) for p in pids}
    if not wanted:
        return {}
    directory = log_dir()
    if not os.path.isdir(directory):
        return {}

    entries = _list_logs(directory)
    if started:
        window = [e for e in entries if _near_any_start(e[1], started)]
        # A window that excludes everything means the window is wrong, not that
        # the processes have no logs.
        if window:
            found = _read_candidates(window, wanted)
            if len(found) == len(wanted):
                return found
            entries = [e for e in entries if e not in window] + window
    return _read_candidates(entries, wanted)


def _list_logs(directory: str) -> List[tuple]:
    """``(stamp, name, path)`` for every log, newest first.

    Ordered by the **filename stamp**, not by mtime. The stamp is in the name, so
    sorting on it needs no ``stat`` call, and that matters: with thousands of logs
    the stats cost more than the reads. A log still being written has an mtime
    that moves under the sort and can land in the wrong place; its stamp is fixed
    at creation, so the order is stable while the file grows.
    """
    entries: List[tuple] = []
    for name in os.listdir(directory):
        if not name.endswith(".log"):
            continue
        stamp = stamp_seconds(name)
        # An unparseable name sorts last rather than first, so it is read only
        # after every log that can be ordered.
        entries.append((stamp if stamp is not None else float("-inf"), name,
                        os.path.join(directory, name)))
    entries.sort(key=lambda e: e[0], reverse=True)
    return entries


def _near_any_start(name: str, started: Dict[int, float]) -> bool:
    stamp = stamp_seconds(name)
    if stamp is None:
        return True  # an unparseable stamp is not evidence of a mismatch
    return any(abs(stamp - when) <= START_WINDOW_SECONDS for when in started.values())


def _read_candidates(entries: List[tuple], wanted: Set[int]) -> Dict[int, Dict[str, Any]]:
    found: Dict[int, Dict[str, Any]] = {}
    for _stamp, name, full in entries:
        if len(found) == len(wanted):
            break
        identity = read_identity(full)
        if not identity:
            continue
        pid = identity.get("pid")
        if pid is None or pid not in wanted or pid in found:
            continue
        identity["started"] = stamp_seconds(name)
        found[pid] = identity
    return found


def ambiguous_reason(
    mesh_name: Optional[str],
    candidates: Sequence[int],
    identities: Dict[int, Dict[str, Any]],
) -> Optional[str]:
    """Explain why a mesh name could not be pinned to one log, or ``None``.

    Returns ``None`` only when **exactly one candidate process was found and its
    log was readable**. Those are two different conditions, and this function used
    to conflate them.

    **The defect this shape exists to fix.** This used to take a list of
    *identities* and treat ``len(matches) == 1`` as resolved. The caller builds
    that list by filtering candidates down to those whose log it could actually
    read, so a candidate whose log was missing or unreadable **vanished from the
    count**: two candidate processes and one readable log reported "not
    ambiguous", with a reason of ``None``. Measured live 2026-10-01 with two
    Studios on one place — ``resolved=False`` alongside ``ambiguous=None``, a
    refusal indistinguishable from a negative result. Same failure class as the
    unreported ``console_writes`` found the same day.

    So the count that decides is the **candidate** count, and a log that could
    not be read is now reported as the reason instead of being absorbed into a
    false certainty. A candidate without a readable log is *not* evidence for
    the process it belongs to.
    """
    count = len(candidates)
    if count == 1:
        only = candidates[0]
        if only in identities:
            return None
        return (
            f"one process reports place {mesh_name!r} but its log could not be read, "
            f"so the identity is unconfirmed (pid {only})"
        )
    if not mesh_name:
        return (
            "this Studio reports no place name, so the log cannot be matched to it; "
            "the log only records a command line"
        )
    uri = _MESH_URI_NAME_RE.match(mesh_name)
    if uri:
        return (
            f"mesh name {mesh_name!r} is a URI launch, and the logs of the candidate "
            "processes do not record an autorecovery path, so the AutoRecovery counter "
            "cannot be compared. They opened a plain unsaved document rather than an "
            "autorecovery copy, so there is no counter to compare"
        )
    if count == 0:
        return f"no Studio log mentions place {mesh_name!r}"
    readable = sum(1 for pid in candidates if pid in identities)
    if readable < count:
        return (
            f"{count} processes report place {mesh_name!r} but only {readable} of their "
            "logs could be read, so the name cannot single one out. An unreadable log is "
            "not evidence for the process it belongs to"
        )
    return (
        f"{count} logs report place {mesh_name!r}, so the name does not "
        "identify one process"
    )


def match_mesh_name(mesh_name: Optional[str], identities: Dict[int, Dict[str, Any]]) -> List[int]:
    """PIDs whose log could correspond to ``mesh_name``.

    Two rules, in order of directness:

    1. The mesh name is the opened file's **basename** for a file launch, and the
       log's command line holds the full path, so a basename match is an exact
       string comparison between two things that already exist.
    2. The mesh name is ``Template_<placeId>_AutoRecovery_<N>.rbxl`` for a URI
       launch. Only the placeId is in both, which identifies the *place* but not
       the *instance*, so this rule is allowed to return several PIDs.
    """
    if not mesh_name:
        return []
    exact = [
        pid
        for pid, identity in identities.items()
        if identity.get("place_path")
        and platform.basename(identity["place_path"]) == mesh_name
    ]
    if exact:
        return exact
    return _mesh_rows_for_place_id(mesh_name, identities)


def _mesh_rows_for_place_id(mesh_name: str, identities: Dict[int, Dict[str, Any]]) -> List[int]:
    uri = _MESH_URI_NAME_RE.match(mesh_name)
    if not uri:
        return []
    place_id = int(uri.group(1))
    candidates = [
        pid for pid, identity in identities.items() if identity.get("place_id") == place_id
    ]
    if len(candidates) < 2:
        return candidates

    # Two or more URI launches of one place. The ``AutoRecovery_N`` in the mesh
    # name is the only thing that separates them, and the log records it on the
    # path-suffixed ``PlaceSessionId`` line - so read that one field, on this
    # handful of logs, rather than declaring the case unresolvable.
    refined = _refine_by_autorecovery_counter(mesh_name, candidates, identities)
    return refined if refined else candidates


def _refine_by_autorecovery_counter(
    mesh_name: str, candidates: List[int], identities: Dict[int, Dict[str, Any]]
) -> List[int]:
    """Narrow URI candidates by the autorecovery path their logs record.

    Returns the narrowed list, or ``[]`` when the log data cannot decide - in
    which case the caller keeps the full candidate list rather than narrowing to
    a guess. A log with no path-suffixed ``PlaceSessionId`` never opened an
    autorecovery document, so it has no counter to offer and simply does not
    narrow.
    """
    wanted = platform.basename(mesh_name)
    matched: List[int] = []
    for pid in candidates:
        log = identities[pid].get("log")
        if not log:
            continue
        path = read_place_session_path(os.path.join(log_dir(), log))
        if path and platform.basename(path) == wanted:
            matched.append(pid)
    return matched





def name_matches_identity(mesh_name: Optional[str], identity: Dict[str, Any]) -> bool:
    """Whether one mesh name belongs to one log identity."""
    if not mesh_name:
        return False
    if identity.get("place_path") and platform.basename(identity["place_path"]) == mesh_name:
        return True
    if identity.get("place_id") is not None:
        return mesh_name.startswith("Template_%d_AutoRecovery_" % identity["place_id"])
    return False


# --------------------------------------------------------------------------- #
# The parent edge: reaching a Studio that reports no place name
# --------------------------------------------------------------------------- #
#
# A play test's server and clients report ``name: null``, so
# :func:`match_mesh_name` returns nothing for them and the whole chain
# ``studio_id -> mesh name -> log -> pid`` stops at its first step. They are not
# reachable by name at all, because a session member opens no document of its
# own - ``name: null`` here is not a missing value, it is the truth.
#
# What does exist is an edge the child states about itself in its own command
# line::
#
#     -task StartServer -localProjectFile <path> -parentPid 19028
#
# ``-parentPid`` names the launching Studio's process outright. No name matching,
# no log sweep, no console write: a child is one hop from a process whose place
# is already known, because the parent is identified by the existing
# log-stated-PID chain (:func:`live_identities`) exactly like any other.
#
# **What it does not do is separate a server from its clients.** The edge says
# which Studio started the test; nothing in any log says which of the unnamed
# mesh rows is the server and which is a client. :func:`resolve_unnamed_studio`
# therefore resolves only when one anchored child and one unnamed row account for
# each other exactly, and otherwise says so - see the note on the counting rule
# there for why guessing here would be the worst possible failure.

#: The tasks only a play test's child processes carry, lowercased because the
#: task is lowercased before comparison, as in ``role_from_command_line``.
#:
#: Deliberately narrow, and the narrowness is load-bearing. Plenty of Studio
#: processes carry a ``-parentPid``: the Roblox launcher process points at
#: whatever started Studio, and a File > New child points at its launcher and
#: then *does* open a place of its own. Keying on ``-parentPid`` alone would put
#: a named Studio into the candidate pool for an unnamed one, which is the exact
#: confusion the name join exists to avoid. The task is what distinguishes "a
#: session member with no document of its own" from "a Studio that opened one".
PLAYTEST_TASKS = frozenset({"startserver", "startclient"})


def is_playtest_task(task: Optional[str]) -> bool:
    """Whether a ``-task`` value is a play test's server or client."""
    return bool(task) and str(task).lower() in PLAYTEST_TASKS


def ancestor_chain(
    pid: int, identities: Dict[int, Dict[str, Any]], live_pids: Set[int]
) -> List[int]:
    """PIDs from ``pid``'s parent outward, as far as the logs carry the edge.

    A play test is two deep - client to server to the Edit Studio that pressed
    Play - so one hop identifies the session but not the document. Walking the
    whole chain is what lets a report say *which place* an unnamed Studio is
    running.

    Every step is the child's own recorded ``-parentPid``, and the walk is
    bounded twice: by ``live_pids`` (a parent that is not running is not an
    ancestor of anything) and by ``seen`` (two logs claiming each other as parent
    would otherwise loop forever, and a loop in a resolver is a hang, not an
    error).
    """
    chain: List[int] = []
    seen = {pid}
    current = pid
    while True:
        parent = identities.get(current, {}).get("parent_pid")
        if not isinstance(parent, int) or parent in seen or parent not in live_pids:
            return chain
        chain.append(parent)
        seen.add(parent)
        current = parent


def playtest_children(
    identities: Dict[int, Dict[str, Any]], live_pids: Set[int]
) -> List[Dict[str, Any]]:
    """Every live play-test process, with its parent edge resolved as far as it goes.

    Returned in pid order, and *every* play-test process is returned whether or
    not the edge anchored - a process whose ``-parentPid`` names nothing live is
    evidence, and swallowing it is how the missing edge would go unreported.

    ``parent_guid_confirms`` cross-checks the child's ``-parentSessionGuid``
    against the ``Session GUID is`` line in the parent log. It is ``None`` when
    either side is missing, because "not checked" and "checked, disagreed" must
    not look the same. **The premise is unverified**: that
    ``-parentSessionGuid`` is the parent *process's* session GUID is read off the
    flag's name and one command line, and was never observed to equal a real
    parent's ``Session GUID is``. So a disagreement is reported and does not veto
    the pid edge, which is stated outright on both sides rather than quietly
    assumed true.
    """
    rows: List[Dict[str, Any]] = []
    for pid in sorted(identities):
        identity = identities[pid]
        task = identity.get("task")
        if not is_playtest_task(task):
            continue

        parent_pid = identity.get("parent_pid")
        parent = identities.get(parent_pid) if isinstance(parent_pid, int) else None
        stated_guid = identity.get("parent_session_guid")
        parent_guid = parent.get("session_guid") if parent else None
        if stated_guid and parent_guid:
            confirms: Optional[bool] = str(stated_guid).upper() == str(parent_guid).upper()
        else:
            confirms = None

        anchored = True
        note = ""
        if not isinstance(parent_pid, int):
            anchored = False
            note = "no -parentPid in this process's own command line"
        elif parent_pid == pid:
            anchored = False
            note = f"-parentPid {parent_pid} names this process"
        elif parent_pid not in live_pids:
            anchored = False
            note = f"-parentPid {parent_pid} names a process that is not running"
        elif parent is None:
            anchored = False
            note = (
                f"-parentPid {parent_pid} is running but has no Studio log yet, "
                "so its place is unknown"
            )

        rows.append({
            "pid": pid,
            "task": task,
            "place_path": identity.get("place_path"),
            "log": identity.get("log"),
            "parent_pid": parent_pid if isinstance(parent_pid, int) else None,
            "parent_task": parent.get("task") if parent else None,
            "parent_place_path": parent.get("place_path") if parent else None,
            "parent_place_id": parent.get("place_id") if parent else None,
            "parent_log": parent.get("log") if parent else None,
            "parent_session_guid": parent_guid,
            "stated_parent_session_guid": stated_guid,
            "parent_guid_confirms": confirms,
            "ancestors": ancestor_chain(pid, identities, live_pids),
            "anchored": anchored,
            "anchor_note": note,
        })
    return rows


def resolve_unnamed_studio(
    identities: Dict[int, Dict[str, Any]], live_pids: Set[int], unnamed_rows: int
) -> Dict[str, Any]:
    """Identify a mesh row that reports ``name: null``, or say why it cannot be.

    ``unnamed_rows`` is how many connected mesh rows carry no name. It is
    **required**, and it is the whole decision: the mesh row carries only ``id``
    and ``name``, so the number of unnamed rows is the only thing on the mesh
    side that distinguishes a server from its clients. Without it the caller
    would be asking "is this the only unnamed Studio?", and answering that with
    "yes" because the pool happened to hold one process would be the exact
    confidently-wrong kill this resolver exists to prevent.

    Returns ``resolved`` plus a ``child`` row, or ``resolved: False`` with a
    ``reason`` naming what is missing and a ``tree`` of every play-test process
    seen, so an unresolved answer still says what the logs *do* know.
    """
    children = playtest_children(identities, live_pids)
    anchored = [child for child in children if child["anchored"]]
    tree = [
        {
            "pid": child["pid"],
            "task": child["task"],
            "parent_pid": child["parent_pid"],
            "anchored": child["anchored"],
            "note": child["anchor_note"],
        }
        for child in children
    ]

    if not anchored:
        if children:
            detail = "; ".join(
                "pid %d: %s" % (child["pid"], child["anchor_note"]) for child in children
            )
        else:
            detail = (
                "no running Studio process carries -task StartServer or -task "
                "StartClient, so there is no -parentPid edge to follow"
            )
        return {
            "resolved": False,
            "reason": (
                "this Studio reports no place name and the logs offer no anchored "
                "play-test child to match it against: " + detail + ". A Studio that "
                "attached with no place open also reports name: null, and no log "
                "field separates that case from a session member"
            ),
            "candidates": [],
            "tree": tree,
        }

    pids = ", ".join(str(child["pid"]) for child in anchored)
    if len(anchored) == 1 and unnamed_rows == 1:
        child = anchored[0]
        return {
            "resolved": True,
            "child": child,
            "reason": None,
            "candidates": [child["pid"]],
            "tree": tree,
        }

    if unnamed_rows < 1:
        # The caller asked about a row with no name while reporting that none are
        # connected. Something upstream miscounted, and answering from this side
        # would bury that rather than surface it.
        return {
            "resolved": False,
            "reason": (
                f"asked to identify a Studio reporting no place name, but "
                f"{unnamed_rows} connected rows report no name, so the mesh answer "
                "and the question disagree; refusing rather than guessing which "
                "reading is wrong"
            ),
            "candidates": [child["pid"] for child in anchored],
            "tree": tree,
        }

    if len(anchored) == 1:
        reason = (
            f"{unnamed_rows} connected mesh rows report no place name and exactly one "
            f"live play-test process is anchored by -parentPid (pid {pids}). The row "
            "being asked about is not necessarily that process: the remaining unnamed "
            "rows belong to Studios that opened no place, and no log field says which "
            "row is which"
        )
    else:
        reason = (
            f"{unnamed_rows} connected mesh rows report no place name and "
            f"{len(anchored)} live play-test processes are anchored by -parentPid "
            f"(pids {pids}). The parent edge says which Studio started the test; it "
            "does not say which unnamed mesh row is the server and which are its "
            "clients, so this is a choice between candidates rather than a join"
        )
    return {
        "resolved": False,
        "reason": reason,
        "candidates": [child["pid"] for child in anchored],
        "tree": tree,
    }
