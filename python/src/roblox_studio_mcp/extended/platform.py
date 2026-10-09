"""Everything that differs between Windows and macOS, in one place.

Why this module exists
----------------------
The platform-specific surface used to be scattered through ``instance.py``,
``logid.py`` and ``locks.py``: PowerShell, ``os.startfile``, ``%LOCALAPPDATA%``,
``RobloxStudioBeta.exe`` in a regex. The worst of those was the binary name in a
regex - ``logid`` would have silently found nothing on a Mac and reported "no
identity" **with no error**, which is the failure mode this project keeps paying
for. Platform assumptions belong in a primitive that can be read, not in a
pattern nobody thinks to check.

What is verified and what is not
--------------------------------
The **parsing** on this module is tested on Windows against macOS-shaped input:
``ps`` rows, ``RobloxStudio`` without ``.exe``, forward-slash paths, the macOS log
and AutoSaves directories. That is most of the risk, because the risk *is* parsing.

The **integration** is not verified. No Mac was available, so nothing here has run
against a real macOS Studio. Treat the macOS branch as correct-by-construction and
unproven, and say so rather than implying it works.

Paths, from Roblox's own documentation rather than guessed
---------------------------------------------------------
====================================  ==========================================
logs                                  ``%LOCALAPPDATA%\\Roblox\\logs``  /
                                      ``~/Library/Logs/Roblox``
Studio AutoSaves and document locks    ``%LOCALAPPDATA%\\Roblox\\RobloxStudio\\AutoSaves`` /
                                      ``~/Library/Application Support/Roblox/RobloxStudio/AutoSaves``
Studio binary                          ``%LOCALAPPDATA%\\Roblox\\Versions\\*\\RobloxStudioBeta.exe`` /
                                      ``/Applications/RobloxStudio.app``
====================================  ==========================================

Sources: Roblox support, "How to Retrieve Roblox Studio Logs", and
``create.roblox.com/docs/studio/debugging`` - both give the macOS paths explicitly -
cross-checked against ``Superwheat/renium``, which uses the same two.

The launcher and the mesh proxy are **different files in the same directory** on
macOS: ``.../MacOS/RobloxStudio`` takes ``--task`` and opens a GUI;
``.../MacOS/StudioMCP`` is the mesh proxy this client launches. They share a
parent and both sound like "the Studio binary", and picking the wrong one either
opens a window that never attaches or exits immediately. ``roblox.py`` holds the
proxy path, this module holds the launcher, and neither substitutes for the other.

Two path traps, one of which is not a trap:

* **``Application Support`` contains a space**, so the AutoSaves path must be
  built with ``os.path.join`` over a list, never by concatenation - which is the
  bug this module already fixed once.
* **The log directory has no space** (``Library/Logs/Roblox``) and is safe. Do
  not "harden" it anyway.

The log *filename* format is treated as identical on both platforms
(``<version>_<UTCstamp>_Studio_<token>_last.log``), so the stamp parsing, the
stamp ordering and the time-window filter port unchanged. **This is the weakest
claim in the module.** It rests on a single Windows-era example in Roblox's
support article that says Mac filenames "look the same"; the 5-hex-digit token
width is unconfirmed on a Mac. If log discovery goes quiet on a Mac, this is the
first thing to check - not the directory.

Do not refactor ``127.0.0.1`` into ``localhost``
----------------------------------------------
On macOS ``localhost`` can resolve to ``::1`` while the mesh listens on IPv4
only, and the connection is then refused. The literal address is already correct
on both platforms, and changing it for tidiness would introduce a bug on the one
platform that cannot be tested here.

What does not port
------------------
``PrintWindow`` capture. Win32 has no counterpart; ``CGWindowListCreateImage`` is
deprecated and requires screen-recording permission. macOS capture therefore stays
on the engine path at ~1.7 s, and the 8-35x win is Windows-only.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set

from .errors import CAPABILITY_DENIED, ToolError

MESH_PORT = 13469

#: Roblox Studio's executable name per platform. The ``.exe`` suffix is what made
#: this a regex hazard: a log banner from a Mac ends ``.../RobloxStudio`` with no
#: suffix, so a pattern requiring ``.exe`` matches nothing and reports no identity
#: rather than an error.
STUDIO_BINARY_WINDOWS = "RobloxStudioBeta.exe"
STUDIO_BINARY_MACOS = "RobloxStudio"

#: Matches a Studio banner line on either platform. The name is alternated rather
#: than suffixed so both spellings are first-class.
BANNER_BINARY = r"(?:RobloxStudioBeta\.exe|RobloxStudio)"

#: A banner line: untimestamped, ending in the Studio binary. The negative
#: lookahead for an ISO date only excludes type-4 FLog lines; a type-1 line
#: (``1712972981.05664,...``) is *tested* and rejected on the binary-name
#: requirement instead. Harmless, but the guard is not doing the job its shape
#: suggests, so it is commented rather than trusted.
#:
#: Restored verbatim after a pruning script deleted it as collateral damage. That
#: script removed a *neighbouring* symbol with a regex spanning to the next blank
#: line, and a non-greedy match under DOTALL reaches much further than the
#: symbol it was aimed at. It took ``BANNER_RE`` with it, and ``logid.py`` then
#: failed at import with ``module 'platform' has no attribute 'BANNER_RE'`` -
#: caught only because the gate was run immediately afterwards. **Deleting code
#: by regex is how you delete the thing you were keeping.**
BANNER_RE = re.compile(
    r"^(?!\d{4}-).*?" + BANNER_BINARY + r"(?:\s+(.*\S))?\s*$", re.MULTILINE
)

#: An executable smaller than this is a partially written or absent install. A
#: directory being replaced still exists and may still contain the ``.exe`` path,
#: so size is the only cheap signal that an install is not mid-update; launching
#: from one produced ``STATUS_DLL_NOT_FOUND`` twice.
#:
#: 100 MB, not a rounder number: this is the value the Windows path was measured
#: against, and it moved to the platform module unchanged. A first draft of this
#: file used 20 MB, which would have quietly lowered a threshold that was set
#: from an actual failure.
MIN_EXE_BYTES = 100 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Platform
# --------------------------------------------------------------------------- #

def is_windows() -> bool:
    """Whether this is Windows. The single platform predicate.

    Everything branches on **this**, positively, and never on ``is_macos()``. Two
    reasons, both learned the hard way:

    * an unpatched call must take the branch that is actually exercised here. A
      function branching on ``is_macos()`` silently takes the *unverified* path when
      the test forgets to patch the other predicate, which is how three of the
      tests below first failed while looking correct.
    * one predicate means one thing to patch and one thing to read.
    """
    return os.name == "nt" or sys.platform.startswith("win")


def is_macos() -> bool:
    """Convenience inverse, for callers that want to assert a platform."""
    return not is_windows()


def home() -> str:
    return os.path.expanduser("~")


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def basename(path: str) -> str:
    """The last component of a path, splitting on **both** separators.

    Not for correctness against ``os.path.basename``, which on both ``ntpath`` and
    ``posixpath`` already treats ``/`` and ``\\`` as separators. It is here so the
    *intent* is explicit at the call site: a Studio log is data about a launch,
    and the separator in that data belongs to whichever machine wrote it rather
    than to whichever machine is reading it. An earlier version of this file
    claimed ``os.path.basename`` mishandled a macOS path on Windows, and a test
    pinned that claim - both were wrong, and the test caught it.
    """
    if not path:
        return ""
    normalised = path.replace("\\", "/").rstrip("/")
    return normalised.rsplit("/", 1)[-1] if "/" in normalised else normalised


def log_dir() -> str:
    """Where Studio writes its per-process logs.

    ``~/Library/Logs/Roblox`` on macOS, per Roblox's support documentation. Player
    and Studio share the directory, so only the ``Studio``-named files matter.

    Joined with ``/`` explicitly, **not** ``os.path.join``. That uses the *host's*
    separator, so on Windows this returned ``/Users/me\\Library\\Logs\\Roblox`` -
    a path that exists on neither platform, and one that cannot even be asserted
    against without knowing the host. Building the target platform's path with the
    target platform's separator makes it correct everywhere and testable anywhere.
    """
    if is_windows():
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
    return "%s/Library/Logs/Roblox" % home()


def autosaves_dir() -> str:
    """Where Studio keeps recovered documents - and therefore the ``.lock`` files.

    The lock is the cheapest available join (``<place>.lock`` names the pid holding
    it), so this being wrong on a platform costs the cheapest identification route
    rather than a convenience. Explicit ``/`` joins, for the reason in
    :func:`log_dir`.
    """
    if is_windows():
        return os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Roblox", "RobloxStudio", "AutoSaves"
        )
    return "%s/Library/Application Support/Roblox/RobloxStudio/AutoSaves" % home()


def autosave_dirs() -> List[str]:
    """The live directory plus ``Archived``, newest-first is the caller's sort."""
    root = autosaves_dir()
    return [root, root + "/Archived"] if not is_windows() else [
        root, os.path.join(root, "Archived")
    ]


def describe_installs() -> List[Dict[str, Any]]:
    """What is actually in ``Versions\\*``, and what each entry is.

    Added because ``studio_exe()`` returning ``None`` meant three different
    things and reported all of them as one - see :func:`studio_exe` for why that
    matters. Measured on this machine, ``Versions`` holds **three** directories
    and only one is Studio:

    ==========================  ==========================================
    ``version-2366ba214ec740ca``  Roblox **Player** - ``RobloxPlayerBeta.exe``
    ``version-6b0e880a1a144428``  **only** ``StudioMCP.exe``, the mesh proxy
    ``version-76e1a02649ad4f35``  the Studio install
    ==========================  ==========================================

    So "no Studio executable here" is the *normal* state of a machine with Player
    installed, not evidence of a half-finished update.
    """
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "Versions")
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        entry: Dict[str, Any] = {"dir": name, "studio_bytes": None, "other_exes": []}
        studio = os.path.join(root, name, STUDIO_BINARY_WINDOWS)
        try:
            entry["studio_bytes"] = os.path.getsize(studio)
        except OSError:
            pass
        try:
            for other in os.listdir(os.path.join(root, name)):
                if other.lower().endswith(".exe") and other != STUDIO_BINARY_WINDOWS:
                    entry["other_exes"].append(other)
        except OSError:
            pass
        entry["is_studio_install"] = (
            entry["studio_bytes"] is not None
            and entry["studio_bytes"] >= MIN_EXE_BYTES
        )
        out.append(entry)
    return out


def studio_exe() -> Optional[str]:
    """The Studio executable, newest complete install.

    Windows scans ``Versions\\*`` and requires a plausible size, because a
    directory being replaced still exists and launching from it produced
    ``STATUS_DLL_NOT_FOUND`` twice. macOS has a single app bundle at a fixed path,
    so there is no version scan and no equivalent race to guard.

    The size floor is a **proxy** for "not mid-update", never a diagnosis of it.
    Callers must not report the cause; see :func:`describe_installs` for what can
    actually be concluded.
    """
    if not is_windows():
        for candidate in (
            "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio",
            "%s/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio" % home(),
        ):
            if os.path.isfile(candidate):
                return candidate
        return None

    candidates: List[tuple] = []
    for entry in describe_installs():
        if entry["studio_bytes"] is None:
            continue
        path = os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Roblox", "Versions",
            entry["dir"], STUDIO_BINARY_WINDOWS,
        )
        try:
            candidates.append((os.path.getmtime(path), entry["studio_bytes"], path))
        except OSError:
            continue
    for _mtime, size, path in sorted(candidates, reverse=True):
        if size >= MIN_EXE_BYTES:
            return path
    return None


# --------------------------------------------------------------------------- #
# Launching
# --------------------------------------------------------------------------- #

def open_uri(uri: str) -> None:
    """Hand a protocol URI to the desktop's registered handler.

    ``os.startfile`` on Windows, ``open`` on macOS. Neither is an executable path,
    so ``subprocess`` with the URI as the program would fail - which is why this is
    a dedicated primitive rather than a call site.

    **Refused on macOS, and that is a deliberate change.** The research behind
    this module found **no evidence that macOS registers the ``roblox-studio:``
    scheme at all**: no ``CFBundleURLTypes`` entry was readable, no Mac forum post
    opens one, and Roblox's own docs document only the two *binary* routes for
    opening a place. ``open`` on an unregistered scheme therefore prints an error
    to stderr and exits non-zero, and this call site discarded both - a launch
    that did nothing, reported as a launch that did nothing. That is the exact
    class of silent failure this project exists to prevent, so it is now loud.

    Not a claim that the scheme is *absent* on macOS, only that it is unevidenced,
    and an unevidenced route is not offered. Launch a place by file with
    :func:`studio_exe`, which works on both platforms and needs no scheme.
    """
    if not is_windows():
        raise ToolError(
            CAPABILITY_DENIED,
            "the roblox-studio: URI scheme is not evidenced on macOS, so this "
            "route is refused rather than silently doing nothing. Launch the "
            "place by file instead: %s --task EditFile --localPlaceFile <path>"
            % studio_exe(),
        )
    os.startfile(uri)  # type: ignore[attr-defined]  # noqa: S606





def terminate(pid: int) -> None:
    """Kill a process, with no chance to clean up. Blocking.

    The **forced** half of the pair, and the fallback after a graceful request
    has been given the grace window to work. ``instance`` stops by calling
    :func:`request_exit` first and only then this, so both platforms are handled
    here.

    Windows: ``Stop-Process -Force``. Note what ``-Force`` actually controls -
    Microsoft's own documentation says it "stops the specified processes without
    prompting for confirmation", and that without it the cmdlet prompts for a
    process the current user does not own. It is **not** a force flag over a
    gentler kill: ``Stop-Process`` terminates either way. That is why the
    graceful half below is a different command, not the same one without
    ``-Force``.

    POSIX: ``kill -9``.
    """
    if is_windows():
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=30,
        )
    else:
        subprocess.run(["kill", "-9", str(int(pid))], capture_output=True, timeout=30)


#: How long a graceful stop request is given before the forced kill follows.
#: A constant because it describes Studio's shutdown behaviour, not the caller's
#: intent; ``instance.terminate_process`` exposes it as the ``grace_seconds``
#: argument it has always had, which used to be only the *wait after* a forced
#: kill.
GRACE_SECONDS = 8.0


def request_exit(pid: int) -> str:
    """Ask a process to exit on its own terms. Blocking. Returns the mechanism.

    Windows: ``taskkill /PID N`` **without** ``/F``. That posts ``WM_CLOSE`` to
    the process's main window, which is the request a user makes by clicking the
    X, and it is the only documented graceful route here. ``Stop-Process``
    without ``-Force`` is deliberately *not* used as the gentle first attempt:
    per Microsoft's own reference it terminates the process either way, and
    ``-Force`` only suppresses the confirmation prompt - so using it would have
    produced a docstring claiming a graceful escalation while the behaviour
    stayed a hard kill. ``taskkill`` without ``/F`` is what actually asks.

    POSIX: ``SIGTERM``, whose default disposition terminates but which Studio
    may catch and handle - the same courtesy, and the same fallback.

    The exit code is ignored on both. A process with no main window, or one
    already exiting, makes ``taskkill`` report failure while the stop still
    takes effect; and on POSIX a signal to a process we do not own raises. The
    caller decides what happened by polling liveness, never by this return, and
    the string it gets back is for reporting rather than for branching.
    """
    target = int(pid)
    if is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(target)],
                capture_output=True, text=True, timeout=30,
            )
        except OSError:
            # `taskkill` is present on every supported Windows, so this is a
            # spawn failure rather than a missing tool. Falling through to the
            # forced kill is the caller's job, not this function's.
            return "taskkill (could not be started)"
        return "taskkill /PID (WM_CLOSE)"
    try:
        os.kill(target, signal.SIGTERM)
    except OSError as exc:
        return "SIGTERM (not delivered: %s)" % exc
    return "SIGTERM"


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #
#
# Both platforms return the same normalised shape, so everything above this line is
# platform-agnostic:
#
#   {"pid": int, "created": str, "command_line": str}
#
# ``created`` is a string on both, and only ``instance.py`` parses it, so the
# parser is the one place that has to know which format it is looking at.

def _powershell(script: str, timeout: float = 30.0) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.stdout or ""


def _powershell_json(script: str, timeout: float = 30.0) -> Any:
    raw = _powershell(f"$ProgressPreference='SilentlyContinue'; {script}", timeout)
    start = raw.find("[")
    if start < 0:
        start = raw.find("{")
    if start < 0:
        return None
    try:
        return json.loads(raw[start:])
    except json.JSONDecodeError:
        return None


#: ``ps -axo pid=,lstart=,command=`` gives a fixed-width start time, so the pid and
#: command line are recovered by slicing rather than by splitting on whitespace -
#: a command line contains spaces. Measured shape from Renium, which reads the same
#: fields on macOS.



def parse_ps_row(line: str) -> Optional[Dict[str, Any]]:
    """One ``ps -axo pid=,lstart=,command=`` row -> a normalised process dict.

    Returns ``None`` for a line that is not a Studio *instance*, so the caller can
    filter without a second predicate. Testable on any platform, which is the point:
    the macOS parsing is verified here even though the macOS integration is not.

    ``lstart`` is **five** whitespace-separated fields (``Mon Sep 28 02:36:04
    2026``), so the row is split 7 ways - pid, five date fields, then the command
    line as one remainder. Splitting three ways, as a first attempt here did, put
    ``created="Mon"``, which is worse than useless: it parses as a string and
    silently loses the time.
    """
    line = line.strip()
    if not line:
        return None
    parts = line.split(None, 6)
    if len(parts) < 7 or not parts[0].isdigit():
        return None
    pid = parts[0]
    created = " ".join(parts[1:6])
    command = parts[6]
    if STUDIO_BINARY_MACOS not in command and STUDIO_BINARY_WINDOWS not in command:
        return None
    if "-task" not in command and "roblox-studio:" not in command:
        return None  # a Studio that is not an instance (the installer, a helper)
    return {"pid": int(pid), "created": created, "command_line": command}


def process_rows() -> List[Dict[str, Any]]:
    """Every running Studio instance, normalised.

    Windows: ``Get-CimInstance Win32_Process`` filtered to the binary name.
    macOS: ``ps -axo``, which needs no privilege and starts far faster than
    PowerShell does - worth knowing, because on Windows the equivalent query costs
    **1.9 s per spawn**, measured, and that dominated a resolve.
    """
    if is_windows():
        data = _powershell_json(
            "Get-CimInstance Win32_Process -Filter \"Name='%s'\" | "
            "Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress"
            % STUDIO_BINARY_WINDOWS
        )
        if data is None:
            return []
        rows = data if isinstance(data, list) else [data]
        return [
            {
                "pid": int(row.get("ProcessId", 0) or 0),
                # CIM datetimes serialise as /Date(ms)/, a millisecond epoch.
                "created": str(row.get("CreationDate") or ""),
                "command_line": row.get("CommandLine") or "",
            }
            for row in rows
        ]

    proc = subprocess.run(
        ["ps", "-axo", "pid=,lstart=,command="],
        capture_output=True, text=True, timeout=30,
    )
    # Exit code is deliberately ignored, and that is the correct choice here.
    # `ps` exits non-zero under conditions that are not failures - and the
    # research recorded a sharper one: the obvious macOS alternative, `pgrep -fl`,
    # **exits 1 when it matches nothing**, which is the normal state of a machine
    # with no Studio running. Treating that as an error would report "could not
    # enumerate processes" for a machine that simply has none. Stdout is parsed and
    # nothing else is trusted, on both platforms.
    #
    # `ps` over `pgrep` is a considered difference from prior art rather than an
    # oversight. `Chrrxs/robloxstudio-mcp` picked `pgrep -fl` deliberately, because
    # the command line arrives as a clean whitespace-delimited field; it gives up
    # per-process start time entirely and substitutes a boot id
    # (`sysctl -n kern.boottime`). This module *needs* start time - identity
    # resolution matches a log's start stamp against process creation, and
    # `parse_ps_row` exists specifically to parse `lstart`'s five fields - so
    # `ps` is the right primitive here and `pgrep` is not.
    #
    # Also inherited from that research and worth keeping: on macOS there is no
    # `MainWindowTitle`, so a window title cannot be a fallback for telling two
    # Studios apart. It never was part of the identity chain here - the PID comes
    # from each process's own `UIThreadNotifier` log line - but the capability gap
    # is real and would bite anyone extending this to use titles.
    out: List[Dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        parsed = parse_ps_row(line)
        if parsed:
            out.append(parsed)
    return out


def _pid_alive_windows(pid: int) -> bool:
    data = _powershell_json(
        "Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
        "Select-Object ProcessId | ConvertTo-Json -Compress" % int(pid)
    )
    if data is None:
        return False
    rows = data if isinstance(data, list) else [data]
    return any(int(r.get("ProcessId", 0) or 0) == int(pid) for r in rows)


#: ``GetExtendedTcpTable`` table class: owner PID for every TCP row. The value
#: the native mesh-holder read uses; see :func:`_mesh_holders_native`.
TCP_TABLE_OWNER_PID_ALL = 5

#: ``MIB_TCP_STATE_ESTAB`` - the only state that means "this pid holds the
#: connection" rather than "a socket exists".
MIB_TCP_STATE_ESTAB = 5


def _mesh_holders_native(port: int) -> Optional[Set[int]]:
    """PIDs with an ESTABLISHED connection to `port`, read from the kernel.

    **Why this exists: `Get-NetTCPConnection` costs 27 s here.**
    Measured 2026-10-09 on Windows Python 3.12, same machine, same moment, same
    seven pids returned by both paths:

    | method | per call | rows read |
    |---|---|---|
    | this one, `iphlpapi!GetExtendedTcpTable` | **31 ms** (v4 only), **89 ms** (v4+v6) | 4,469 v4 |
    | `Get-NetTCPConnection` through PowerShell | **27.0 s** | same table |

    About **300x**, and the reason is not the query - both reach the same kernel
    table. It is that PowerShell materialises every row as a .NET
    `NetTCPConnection` object and then pushes each one through a `Where-Object`
    scriptblock, re-parsed per row: the table held 4,469 rows at the time it was
    last measured and has been seen at 14,948, so that is thousands of object
    allocations plus thousands of scriptblock invocations, to answer a question
    about one port.

    **The native figure is not a constant, and an earlier version of this
    docstring said 14.5 ms.** It does not hold: re-measured, the same call is
    31 ms for IPv4 and 89 ms with both families, and the difference is the table
    size, which tracks how many connections the machine has open. So this is a
    300x improvement with a *variable* absolute cost, not a fixed 14.5 ms - and
    anyone quoting a single number should quote the range.

    **`argtypes` is not optional here, and omitting it fails silently.** The
    first version of this function left `argtypes` undeclared; ctypes guessed
    the pointer parameter, the call returned success, and the buffer read back
    as a plausible **one-row** table with a plausible pid - no exception, no
    non-zero return. That wrong answer only surfaced because it was diffed
    against the PowerShell answer. Declare the prototype, or do not write this
    function.

    Ports come back in **network byte order**, so the comparison is against
    `socket.htons(port)`, not `port`. Getting that wrong yields zero matches
    rather than wrong ones, which makes it the harmless half of the trap.

    `None` means "not available" - not Windows, or the call failed - and the
    caller falls back to the shell query. A missing native path degrades to the
    one that works, which is slower and correct, rather than to an error.
    """
    if not is_windows():
        return None
    try:
        import ctypes
        import socket

        # `windll` is Windows-only in pyright's stubs, so it is reached through
        # getattr rather than attribute access - the same pattern as the reaper
        # in `roblox.py`. It is guaranteed to exist here: the `is_windows()` gate
        # above already ran. Returning None on absence keeps the fallback
        # honest rather than guessing.
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        iphlpapi = windll.iphlpapi
        iphlpapi.GetExtendedTcpTable.restype = ctypes.c_ulong
        iphlpapi.GetExtendedTcpTable.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ]

        class _RowV4(ctypes.Structure):
            _fields_ = [
                ("dwState", ctypes.c_ulong),
                ("dwLocalAddr", ctypes.c_ulong),
                ("dwLocalPort", ctypes.c_ulong),
                ("dwRemoteAddr", ctypes.c_ulong),
                ("dwRemotePort", ctypes.c_ulong),
                ("dwOwningPid", ctypes.c_ulong),
            ]

        class _RowV6(ctypes.Structure):
            # NOT the v4 order. In v6 the two 16-byte addresses and their scope
            # ids come first, and state and ports come after them. Swapping the
            # two layouts reads ports out of address bytes - plausible numbers,
            # wrong answer, no error.
            _fields_ = [
                ("ucLocalAddr", ctypes.c_ubyte * 16),
                ("dwLocalScopeId", ctypes.c_ulong),
                ("ucRemoteAddr", ctypes.c_ubyte * 16),
                ("dwRemoteScopeId", ctypes.c_ulong),
                ("dwState", ctypes.c_ulong),
                ("dwLocalPort", ctypes.c_ulong),
                ("dwRemotePort", ctypes.c_ulong),
                ("dwOwningPid", ctypes.c_ulong),
            ]

        row_size = {2: ctypes.sizeof(_RowV4), 23: ctypes.sizeof(_RowV6)}
        want = socket.htons(port)
        found: Set[int] = set()

        for af in (2, 23):
            # One sizing call, then one real call. The buffer is sized by the
            # kernel, so there is no retry loop to get wrong.
            size = ctypes.c_ulong(0)
            iphlpapi.GetExtendedTcpTable(
                None, ctypes.byref(size), 0, af, TCP_TABLE_OWNER_PID_ALL, 0
            )
            if not size.value:
                continue
            buf = ctypes.create_string_buffer(size.value)
            used = ctypes.c_ulong(size.value)
            rc = iphlpapi.GetExtendedTcpTable(
                buf, ctypes.byref(used), 0, af, TCP_TABLE_OWNER_PID_ALL, 0
            )
            if rc != 0:
                continue
            # `dwNumEntries` is a bare DWORD ahead of the rows, and the struct's
            # array is declared size 1 only so the type exists. Rows are read by
            # offset from the buffer rather than through the struct array, so
            # reading past a declared length cannot happen.
            head = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong)).contents.value
            for index in range(head):
                base = 4 + index * row_size[af]
                row = (
                    _RowV4.from_buffer_copy(buf, base)
                    if af == 2
                    else _RowV6.from_buffer_copy(buf, base)
                )
                if row.dwState != MIB_TCP_STATE_ESTAB:
                    continue
                if row.dwRemotePort != want:
                    continue
                if row.dwOwningPid:
                    found.add(int(row.dwOwningPid))
        return found
    except Exception:  # noqa: BLE001 - a missing native path is not an error
        return None


def attached_pids(studio_pids: Optional[List[int]] = None) -> List[int]:
    """Pids holding an established connection to the mesh on :data:`MESH_PORT`.

    Filtered to Studio processes, because the proxy and the listener are holders
    too, and neither is a Studio.

    On macOS this uses ``lsof`` rather than anything Python can do portably: the
    mesh is a loopback WebSocket, so the owning process is only visible through the
    system's own socket table. Unverified on macOS.

    ``studio_pids`` narrows the intersection when the caller already knows which
    processes are in play. Passing it skips the process enumeration entirely -
    which matters more than it looks, because that enumeration is the *other*
    PowerShell spawn, and a caller asking one membership question about an
    already-known Studio pid should not pay for a full list it does not need.
    """
    holders = _mesh_holders_native(MESH_PORT)
    if holders is None:
        holders = _mesh_holders_shell(MESH_PORT)
    if studio_pids is None:
        studio = {row["pid"] for row in process_rows()}
    else:
        studio = {int(p) for p in studio_pids}
    return sorted(holders & studio)


def _mesh_holders_shell(port: int) -> Set[int]:
    """The socket query, through PowerShell or lsof. The slow path.

    Kept because :func:`_mesh_holders_native` returns ``None`` whenever the
    native read is unavailable, and a machine that cannot read the table natively
    is still a machine that needs an answer. Also kept as the parity reference:
    the test suite asserts the two agree rather than trusting the struct layout,
    because that is the only way a silent ctypes regression gets caught.
    """
    holders: Set[int] = set()
    if is_windows():
        data = _powershell_json(
            "Get-NetTCPConnection -RemotePort %d -ErrorAction SilentlyContinue | "
            "Where-Object { $_.State -eq 'Established' } | "
            "Select-Object -ExpandProperty OwningProcess -Unique | ConvertTo-Json -Compress"
            % port
        )
        holders = {int(p) for p in (data if isinstance(data, list) else [data] or [])
                   if p is not None}
    else:
        proc = subprocess.run(
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:ESTABLISHED", "-t"],
            capture_output=True, text=True, timeout=30,
        )
        # Return code ignored for the same reason as `ps` above: "nothing is
        # holding the port" is the normal state of a machine with no Studio
        # running, and an empty answer is not a failure.
        #
        # `-nP` matters more than it looks: without it `lsof` resolves the port
        # through `/etc/services` and does reverse lookups, so a connection to
        # 13469 comes back as a service name rather than a number.
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                holders.add(int(line))
    return holders


def pid_alive(pid: int) -> bool:
    """Whether a pid is still running."""
    if is_windows():
        return _pid_alive_windows(pid)
    try:
        os.kill(int(pid), 0)
    except (OSError, ProcessLookupError):
        return False
    return True


