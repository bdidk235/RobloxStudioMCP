"""A Studio lock file names the PID holding it. The log is not needed at all.

Found by chasing a problem that turned out not to be a problem. The join this
project built is ``studio_id`` -> mesh name -> that process's log -> the log's own
PID line. Every step is host-side, but the log was load-bearing. It is not:

    %LOCALAPPDATA%\\Roblox\\RobloxStudio\\AutoSaves\\<place>.lock

holds the owning PID and some metadata, **newline-separated**. The lock's
*filename* is the same unique document name the mesh reports, and its *contents*
are the PID owning it. So the shortest correct join is:

    studio_id -> mesh name -> <mesh name>.lock -> first field

Exact, no log read, and it works when the document has an AutoRecovery entry -
which is the case that made ``studio_id`` -> PID look unresolvable.

**The layout documented here before was wrong, and the correction is measured.**
This file claimed ``pid | processName | machineName | sessionGuid | |``. Reading
two live lock files byte for byte on this machine:

    31 32 33 32 34 0a 52 6f 62 6c 6f 78 ...   ->  "12324\\nRobloxStudioBeta\\n
                                                 DESKTOP-IH0RL4D\\n<guid>\\n\\n"

**No ``|`` byte appears in either file.** The separator is ``\\n``. The join was
never affected, because the PID is the first field either way, which is exactly
why the wrong documentation went unnoticed.

The consequence was a silent capability loss: ``process``, ``machine`` and
``session`` came back ``None`` for every real lock, because the parser only read
the first line and split it on a separator real files never contain. Fixed
2026-09-30 on both sides (user-approved paired edit): the parser reads one field
per line and keeps the single-line pipe shape as a fallback, so the old
fixtures still pass. The session GUID is now a second independent record to
cross-check a log-derived identity - the same kind of second witness that makes
the log chain trustworthy. Wiring it into the join itself is a separate step,
deliberately not taken here: parsing changes field values, joining on them
changes resolutions. The test fixture carried the same wrong layout, so it
agreed with the doc and disagreed with reality - a reminder that a fixture
written from prose inherits the prose's errors.

**It does not separate two concurrent URI launches of one place.** Both report the
mesh name ``Place1``, neither holds a lock, so this resolves 0 of 3 on that
scenario. A lock only exists for a document Studio was actually *recovering*; a
URI launch that opened cleanly writes one only 1 time in 16 logs. An earlier
version of this docstring claimed the join worked for URI launches in general,
which the measurement contradicts.

Measured on this machine: ``Template_95206881_AutoRecovery_3.rbxl.lock`` names pid
12324, which was live and attached.

Two traps, both real here:

* **A lock outlives its process.** ``84633881964039_AutoRecovery_0.rbxl.lock`` was
  left behind naming pid 18896, long dead. Trusting it returns a dead PID, so
  every PID is checked against the live set and a lock whose owner is gone is
  reported stale rather than used.
* **Not every document holds a lock.** A Studio that opened a plain unsaved
  ``Place1`` holds none, and its mesh name carries no identity either - that
  name is a document *title*, shared by anything, so it is unidentifiable rather
  than merely hard to look up.

This is the same place Studio uses to stop two windows writing one file, so its
lifetime is a deliberate signal: it exists exactly while a process owns the
document.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from . import platform


def autosaves_dir() -> str:
    """Where Studio keeps its document locks. See :func:`platform.autosaves_dir`."""
    return platform.autosaves_dir()


def lock_name_for(place_name: str) -> str:
    """The lock Studio would hold for a mesh-reported place name.

    The mesh reports a basename and the lock is a basename plus ``.lock``, so this
    is a plain string append. Names are Studio's own, so no sanitising is needed;
    anything containing a path separator is rejected rather than joined on.
    """
    if not place_name or platform.basename(place_name) != place_name:
        return ""
    return place_name + ".lock"


def parse_lock(text: str) -> Optional[Dict[str, Any]]:
    """``{pid, process, machine, session}`` from a lock file, or ``None``.

    ``None`` rather than a partial guess: a lock this cannot read is a lock this
    must not answer a join with.

    **Split on newlines first, pipes second.** Real locks are one field per line
    (measured: no ``|`` byte in either live file - see the module docstring), so
    the fields are the first four non-blank lines. A single line containing
    ``|`` falls back to the old pipe split, which keeps the legacy fixtures
    passing. Either way the PID is matched by shape rather than position: a
    document claimed by another user puts something other than the PID in field
    0, so the fields are scanned for the first digit run instead of indexing
    field 0. Only the PID gates: no digit run anywhere means ``None`` rather
    than a partial guess, because a lock this cannot read is a lock this must
    not answer a join with.
    """
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return None
    first = lines[0]
    if "|" in first:
        fields = [field.strip() for field in first.split("|") if field.strip()]
    else:
        fields = lines[:4]
    pid: Optional[int] = None
    for index, field in enumerate(fields):
        if field.isdigit() and pid is None:
            pid = int(field)
            if index == 0:
                break  # the common shape; stop before considering later fields
    if pid is None:
        return None
    return {
        "pid": pid,
        "process": fields[1] if len(fields) > 1 else None,
        "machine": fields[2] if len(fields) > 2 else None,
        "session": fields[3] if len(fields) > 3 else None,
    }


def read_lock(name: str) -> Optional[Dict[str, Any]]:
    """Read one lock file, or ``None`` if absent or unreadable.

    ``None`` on failure is deliberate: a lock being written or removed is normal,
    and raising would fail a join over a transient condition.
    """
    if not name:
        return None
    try:
        with open(os.path.join(autosaves_dir(), name), "r", encoding="utf-8",
                  errors="ignore") as handle:
            return parse_lock(handle.read())
    except OSError:
        return None


def pid_from_lock(
    place_name: str, live_pids: Optional[Set[int]] = None
) -> Dict[str, Any]:
    """Resolve a mesh-reported place name to a PID through its lock file.

    ``live_pids`` is the set of running Studio PIDs. Passing it is not optional in
    spirit: without it a **stale lock** naming a process that exited days ago
    answers the join, which is the one way this can produce a confidently wrong
    PID. When the set is given, a lock naming anything outside it is reported
    ``stale`` and never returned as a match.
    """
    name = lock_name_for(place_name)
    if not name:
        return {"resolved": False,
                "error": f"place name {place_name!r} is not a bare filename, so it "
                         "cannot name a lock file"}
    lock = read_lock(name)
    if lock is None:
        return {"resolved": False, "lock": name,
                "error": f"no readable lock named {name!r}; this document holds no "
                         "lock, which is the case for a plain unsaved document"}
    pid = lock["pid"]
    if live_pids is not None and pid not in live_pids:
        return {"resolved": False, "lock": name, "pid": pid, "stale": True,
                "error": f"lock {name!r} names pid {pid}, which is not running; it is "
                         "a leftover from a closed Studio"}
    return {"resolved": True, "pid": pid, "lock": name,
            "session": lock.get("session"), "machine": lock.get("machine")}


def live_locks(live_pids: Optional[Set[int]] = None) -> Dict[int, str]:
    """PID -> lock filename, for every lock whose owner is running.

    With ``live_pids`` omitted this includes stale locks, which is the useful
    behaviour for *diagnosing* (a stale lock is evidence of a crash) and the
    wrong behaviour for joining, so joining always passes the set.
    """
    directory = autosaves_dir()
    found: Dict[int, str] = {}
    if not os.path.isdir(directory):
        return found
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".lock"):
            continue
        lock = read_lock(name)
        if lock is None:
            continue
        pid = lock["pid"]
        if live_pids is not None and pid not in live_pids:
            continue
        found.setdefault(pid, name)
    return found


def names_from_locks(live_pids: Optional[Set[int]] = None) -> List[str]:
    """Mesh-shaped place names for every live lock, i.e. ``<name>.lock`` stripped.

    This is the reverse direction: given a PID, the document name the mesh would
    report for it. It is what lets a *launch* identify its own process without
    waiting for the mesh to attach.
    """
    return [name[: -len(".lock")] for name in live_locks(live_pids).values()]
