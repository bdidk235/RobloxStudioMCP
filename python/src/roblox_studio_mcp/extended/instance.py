"""Launching, listing and stopping Studio instances, host-side.

Why a tool rather than shell commands
-------------------------------------
Every one of these steps was done by hand during development, and each carries a
trap that is invisible until it bites:

* **Studio only attaches to the mesh with a place open.** A plain launch joins
  nothing and reports ``name: null``, which looks like a transport fault.
* **The version directory can be swapped mid-launch.** Roblox updates in the
  background; launching against a directory that is being replaced gives exit
  code ``-1073741515`` (``STATUS_DLL_NOT_FOUND``) because the executable is
  deleted out from under the process. Measured here twice.
* **One place can be many processes.** ``-task StartServer`` spawns a separate
  ``-task StartClient`` Studio per player, so a process count is not a place
  count, and a title match is not an identity.
* **Instances are expensive.** Five concurrent Studios pushed this machine to
  its 31.3 GB commit ceiling, at which point ``VirtualAlloc`` began failing and
  processes died, including Studio itself and unrelated tooling. Treat the
  process count as a budget, not a detail.

Choosing a place to open
------------------------
Hardcoding a baseplate path is fragile: Roblox's template autosaves carry a
rotating suffix and get pruned, so a pinned filename disappears. They are
discovered by glob instead, newest first, and can be overridden with
``$ROBLOX_STUDIO_BASEPLATE``. They are recovery copies rather than a template
meant for reuse, so pass an explicit ``place_path`` when the content matters.

Identifying an instance
-----------------------
``studio_id`` comes from the proxy and is re-minted on restart, so it addresses
but does not identify. To stop an instance you need its OS process, and the
join is:

1. ``Get-CimInstance Win32_Process`` for creation time, command line and role.
2. ``Get-NetTCPConnection -RemotePort 13469`` for the processes actually
   attached to the MCP mesh, filtered to ``RobloxStudioBeta``.
3. When the window title or place name is ambiguous, print a unique token from
   the ``studio_id`` and find the per-process log under
   ``%LOCALAPPDATA%\\Roblox\\logs`` that contains it. The log's filename encodes
   that process's start time. **This writes a short line to the console**, so it
   is used only when the cheap join is ambiguous.

This is all host-side. Nothing is written into the DataModel, so it cannot reach
the place file, a published place, or a team create.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from ..roblox import RobloxStudio
from . import logid, platform

#: An executable smaller than this is a partially written or absent install.
#: Re-exported from :mod:`platform`, where it is defined.
MIN_EXE_BYTES = platform.MIN_EXE_BYTES

#: Optional override for where to find a baseplate, so a caller can point at a
#: real place instead of a generated one without editing this file.
BASEPLATE_ENV = "ROBLOX_STUDIO_BASEPLATE"

#: A Studio log filename carries its start time, e.g.
#: ``0.741.19.7411056_20260929T230231Z_Studio_C16FB_last.log``. The stamp is UTC
#: while ``CreationDate`` is local, so they must be converted before comparing.
#: Parsed by :func:`logid.stamp_seconds`; the pattern that used to live here was
#: dead and took the ROLE block below with it when a pruning script removed it by
#: regex. See the note on ``platform.BANNER_RE`` for why that is now forbidden.

#: Launch roles, taken from ``-task`` in the command line.
ROLE_EDIT = "edit"
ROLE_SERVER = "server"
ROLE_CLIENT = "client"
ROLE_UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Host-side process facts
# --------------------------------------------------------------------------- #

#: Seconds a process-list answer may be reused.
#:
#: **One PowerShell spawn costs 1.9s on this machine**, measured, so a poll loop
#: that asks twice a second is really asking every two seconds and waiting. The
#: process list changes on the order of minutes, and a caller looking for a
#: *newly* appeared pid cannot be misled by a couple of seconds of staleness - it
#: is looking for something that was not there a moment ago.
#:
#: Kept short deliberately. This is not a cache of anything slow, it is a
#: suppression of a spawn that costs more than the thing being polled for.
_PROCESS_CACHE_SECONDS = 2.0

_process_cache: Dict[str, Any] = {"at": 0.0, "rows": None}


def _process_rows(max_age: float = _PROCESS_CACHE_SECONDS) -> List[Dict[str, Any]]:
    """``Get-CimInstance`` rows for Studio processes, briefly cached."""
    now = time.monotonic()
    if _process_cache["rows"] is not None and now - _process_cache["at"] < max_age:
        return _process_cache["rows"]
    rows = platform.process_rows()
    _process_cache["rows"] = rows
    _process_cache["at"] = now
    return rows


def invalidate_process_cache() -> None:
    """Drop the cached process list, so the next call re-reads it.

    Needed when a caller has just started or stopped a process and must not be
    served the answer from before that.
    """
    _process_cache["rows"] = None
    _process_cache["at"] = 0.0


def list_studio_processes(
    with_attachment: bool = True, max_age: float = _PROCESS_CACHE_SECONDS
) -> List[Dict[str, Any]]:
    """Every running ``RobloxStudioBeta``, with its role and mesh attachment.

    ``with_attachment=False`` skips the mesh-connection lookup, and that is worth
    far more than it looks. Attachment costs **9.5s** here - a
    ``Get-NetTCPConnection`` plus a second ``Get-CimInstance``, at 1.9s a spawn -
    against 2.1s for the process list itself, so the "cheap" call is still
    PowerShell-bound but four times cheaper.

    Callers that are polling for a change, or only need PIDs and creation times,
    should pass ``False`` and rely on ``max_age``. The resolver's
    ``attached`` field is informational; nothing joins on it.
    """
    rows = _process_rows(max_age)
    attached = set(attached_studio_pids()) if with_attachment else set()

    out: List[Dict[str, Any]] = []
    for row in rows:
        # `platform.process_rows` normalises both platforms to the same shape, so
        # nothing above this line needs to know which one it is running on.
        pid = int(row.get("pid", 0) or 0)
        cmd = row.get("command_line") or ""
        out.append(
            {
                "pid": pid,
                "role": role_from_command_line(cmd),
                "attached_to_mesh": (pid in attached) if with_attachment else None,
                "place_file": place_from_command_line(cmd),
                "created": str(row.get("created") or ""),
            }
        )
    out.sort(key=lambda r: r["created"] or r["pid"])
    return out


def attached_studio_pids() -> List[int]:
    """PIDs of Studios holding an established connection to the mesh.

    ``StudioMCP.exe`` proxies also hold connections, and the listener is itself
    a connection holder, so results are filtered to Studio processes.
    """
    return platform.attached_pids()


def role_from_command_line(cmd: str) -> str:
    """Map a command line to a role.

    Two shapes exist. A file or test launch carries ``-task X``. A URI launch
    carries the ``roblox-studio:1+task:EditPlace+...`` string **as the argument**,
    with no ``-task`` flag at all, so a ``-task``-only parse reports every
    URI-launched Studio as ``unknown``. The task is read out of the URI here.
    """
    text = cmd or ""
    match = re.search(r"-task\s+(\S+)", text)
    task = match.group(1) if match else None
    if task is None:
        # URI form: task:EditPlace appears between '+' separators.
        uri = re.search(r"(?:^|\s)roblox-studio:1\+", text)
        if uri:
            inner = re.search(r"\+task:(\w+)", text)
            task = inner.group(1) if inner else None
    if not task:
        return ROLE_UNKNOWN
    task = task.lower()
    if task.startswith("startserver"):
        return ROLE_SERVER
    if task.startswith("startclient"):
        return ROLE_CLIENT
    if task.startswith("edit") or task == "editplace":
        return ROLE_EDIT
    return ROLE_UNKNOWN


def place_from_command_line(cmd: str) -> Optional[str]:
    """Name the place a process has open, whichever launch route it used.

    File launches name a path, so the basename is used. URI launches carry only
    an id and Studio names the place itself, so nothing can be derived here and
    this returns ``None``; the mesh's ``name`` is the only source for those, and
    it is ``None`` too while the place is still opening.

    The path argument is read with :func:`logid._path_after` rather than a
    ``(\\S+)`` capture. ``\\S+`` stops at the first space, which is harmless for
    this machine's own paths (they are under ``%TEMP%``) and wrong for any place
    whose directory contains one - a real ``My Places`` folder, or a macOS
    ``Application Support``. It returned a truncated path with no error, so the
    basename came out as a fragment and never matched a mesh name.
    """
    text = cmd or ""
    for flag in ("--localPlaceFile", "-localProjectFile"):
        raw = logid._path_after(text, flag)  # noqa: SLF001 - same module family
        if raw:
            path = raw.replace("/", os.sep)
            return platform.basename(path) or path
    return None


#: How long a launch waits for the new Studio to attach to the mesh. A constant
#: because it describes Studio's behaviour, not the caller's intent. Measured: a
#: fresh instance on an already-warm place joins in roughly 20-25s, so 75s is
#: generous without approaching the 120s client timeout.
LAUNCH_WAIT = 75.0

# --------------------------------------------------------------------------- #
# Version selection
# --------------------------------------------------------------------------- #

def find_studio_exe() -> str:
    """Return the newest *complete* Studio executable.

    Windows scans ``Versions\\*`` and requires a plausible size, because a
    directory being replaced still exists and launching from it produced
    ``STATUS_DLL_NOT_FOUND`` twice. macOS has one app bundle at a fixed path, so
    there is no version scan and no equivalent race to guard - which is why
    :func:`platform.studio_exe` carries the size check itself rather than
    duplicating it here per platform.

    **The failure message reports evidence, never a cause.** It used to say
    "An update may be in progress; wait for it to finish", which is a guess the
    code cannot support: all it knows is that no file cleared ``MIN_EXE_BYTES``.
    Measured on this machine, that state is *normal* - ``Versions`` held three
    directories and only one was Studio (the others were Roblox Player, and
    ``StudioMCP.exe`` alone). Telling an agent to wait for an update that is not
    happening sends it to wait forever, and it happened here: the same message
    was returned after the executable had been **renamed**, which is neither an
    update nor retryable.

    So the three distinguishable states get three distinguishable messages, each
    naming what was actually found, because the recoveries differ: install
    Studio, wait for a real update, or restore a moved file.
    """
    exe = platform.studio_exe()
    if exe:
        return exe

    if not platform.is_windows():
        raise FileNotFoundError(
            "no RobloxStudio.app in /Applications or ~/Applications. "
            "Install Studio from the Roblox website, then retry."
        )

    entries = platform.describe_installs()
    if not entries:
        raise FileNotFoundError(
            "no Roblox install directory at "
            f"{os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Roblox', 'Versions')}. "
            "Install Studio from the Roblox website, then retry."
        )

    # A directory carrying Studio's own files but not its executable is a
    # **broken Studio install**, not an absent one, and the two need opposite
    # advice. Measured here: `version-76e1a02649ad4f35` still holds
    # `RobloxStudioInstaller.exe` and `NativeDialog.exe` with the executable
    # renamed away, so "install Studio" would have been wrong - Studio was
    # installed, and the file had been moved.
    studio_markers = ("RobloxStudioInstaller.exe", "NativeDialog.exe")
    broken = [
        e for e in entries
        if e["studio_bytes"] is None
        and any(m in e["other_exes"] for m in studio_markers)
    ]
    partial = [
        e for e in entries
        if e["studio_bytes"] is not None and not e["is_studio_install"]
    ]

    def _summarise(entry: Dict[str, Any]) -> str:
        if entry["is_studio_install"]:
            return "Studio, %d MB" % (entry["studio_bytes"] // (1024 * 1024))
        if entry["studio_bytes"] is not None:
            return "RobloxStudioBeta.exe at %d MB, under the %d MB floor" % (
                entry["studio_bytes"] // (1024 * 1024),
                platform.MIN_EXE_BYTES // (1024 * 1024),
            )
        exes = [e for e in entry["other_exes"] if e not in studio_markers]
        return ", ".join(exes[:2]) or "no Roblox executables"

    listing = "; ".join(
        "%s: %s" % (e["dir"], _summarise(e)) for e in entries[:4]
    )

    if partial:
        advice = (
            "That is what a half-extracted update looks like: wait for it to "
            "finish, or restore the file if something moved it."
        )
    elif broken:
        advice = (
            "A Studio install directory is present but RobloxStudioBeta.exe is "
            "missing from it, so this is a broken install rather than an absent "
            "one. Restore the executable, or reinstall Studio over it."
        )
    else:
        advice = (
            "Only Roblox Player appears to be installed. Install Studio from the "
            "Roblox website; waiting will not help."
        )
    raise FileNotFoundError(
        "no complete Studio executable. Found: %s. %s" % (listing, advice)
    )


# --------------------------------------------------------------------------- #
# Places
# --------------------------------------------------------------------------- #
#
# Two ways to open a place, both measured working on this machine:
#
# 1. **URI**, via the `roblox-studio:` protocol handler. The whole thing is
#    three keys: `roblox-studio:1+task:EditPlace+placeId:<id>+universeId:<id>`.
#    Studio fetches the place from the asset service, so there is no temp copy
#    and no version-directory discovery. The result reports `name: "Place1"`
#    with `PlaceId: 0`, because an unsaved place has no id yet.
#
# 2. **File**, via `--task EditFile --localPlaceFile <path>`. Needed to open a
#    specific local `.rbxl`, which is the only route for a place that exists
#    only on this disk.
#
# An earlier URI attempt used an eleven-key prefix copied from a URI Studio had
# produced, and it opened an "Error" dialog without ever attaching. **The key
# count was the problem, not the route.** So the prefix stays minimal: every
# extra key is another chance to be rejected, and none of them is required.
# Do not "restore" the full URI on the assumption it is more correct.
#
# The prefix appears doubled in some samples
# (`roblox-studio:roblox-studio:1+...`). That is a copy artifact of the protocol
# handler, and it does also work, but the single form is the canonical one and
# is what is built here.


#: The minimal, measured-working URI shape is four keys::
#:
#:     roblox-studio:1+task:EditPlace+placeId:<id>+universeId:0
#:
#: Provenance, kept apart on purpose. **Key count of four** is measured here: an
#: earlier eleven-key prefix failed with an ``Error`` dialog and never attached.
#: The *key* being required is the user's own experience, not a measurement from
#: this project.
#:
#: **Both universe values are measured to open the place** — ``0`` six times out
#: of six, the place's real id eight out of eight, zero errors. An earlier claim
#: here and in ``TODO.md`` that the real id "failed 3 times in 16" did not
#: reproduce and is withdrawn. ``0`` remains the explicit value for "this place,
#: no universe context" — what a template is, and what *File > New* emits.
#: Evidence: ``TODO.md``, *The universe id is a function of the place id*.
URI_UNIVERSE_ID = 0

#: Public endpoint mapping a place to its universe. Unauthenticated.
UNIVERSE_API = "https://apis.roblox.com/universes/v1/places/{place_id}/universe"


async def build_launch_uri(place_id: int, universe_id: Optional[int] = None) -> str:
    """Build the ``roblox-studio:`` URI for a place, deriving its universe.

    ``universe_id`` defaults to ``None``, meaning *ask* :func:`resolve_universe_id`.
    A place's universe is a function of its place id, so deriving it beats
    requiring a value the caller has to already know. Pass ``0`` explicitly for
    "this place, no universe context".

    Async because the default performs I/O; a failed lookup raises
    :class:`UniverseLookupError` rather than producing a malformed URI.

    Launch it with :func:`launch_via_uri`, or ``os.startfile`` directly. Note
    ``subprocess.Popen`` cannot dispatch a protocol handler and raises
    ``FileNotFoundError``.
    """
    if universe_id is None:
        universe_id = await resolve_universe_id(place_id)
    return (
        f"roblox-studio:1+task:EditPlace"
        f"+placeId:{int(place_id)}"
        f"+universeId:{int(universe_id)}"
    )


class UniverseLookupError(RuntimeError):
    """A place's universe id could not be resolved.

    Carries ``attempts`` so a caller can tell "one unlucky call" from "this place
    has no universe", which are different problems with different fixes.
    """

    def __init__(self, place_id: int, attempts: int, reason: str) -> None:
        super().__init__(
            f"could not resolve a universe id for place {place_id} after "
            f"{attempts} attempt(s): {reason}"
        )
        self.place_id = place_id
        self.attempts = attempts
        self.reason = reason


async def resolve_universe_id(
    place_id: int,
    *,
    retries: int = 3,
    delay: float = 0.5,
) -> int:
    """Ask Roblox which universe a place belongs to. Unauthenticated ``GET`` of
    :data:`UNIVERSE_API`, which returns ``{"universeId": N}``.

    Retries because a single fast call is the failure mode worth designing
    against on a launch path: a network blip would otherwise surface as "could
    not open the place" and send the caller looking at the URI. Each retry waits
    ``delay``, doubling.

    Raises :class:`UniverseLookupError`, which carries ``attempts``. A response
    with no ``universeId`` is a failure, never a silent ``0``.
    """
    import json
    import urllib.error
    import urllib.request

    url = UNIVERSE_API.format(place_id=int(place_id))
    attempts = max(1, int(retries))
    last = "no attempt made"

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            value = payload.get("universeId")
            if value is None:
                last = f"response carried no universeId: {payload!r}"
            else:
                return int(value)
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last = f"network error: {exc.reason}"
        except (ValueError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"

        if attempt < attempts:
            await asyncio.sleep(delay * (2 ** (attempt - 1)))

    raise UniverseLookupError(int(place_id), attempts, last)


async def launch_via_uri(place_id: int, universe_id: Optional[int] = None) -> None:
    """Hand the URI to the registered protocol handler.

    ``os.startfile`` rather than ``Popen``: a protocol URI is not an executable,
    and ``Popen`` raises ``FileNotFoundError`` for one.

    Async, because the universe is derived from the place unless one is given -
    see :func:`build_launch_uri`.
    """
    platform.open_uri(await build_launch_uri(place_id, universe_id))


def _autosave_dirs() -> List[str]:
    return platform.autosave_dirs()


def find_baseplate() -> Optional[str]:
    """Newest discovered baseplate, or ``None``.

    Honours ``$ROBLOX_STUDIO_BASEPLATE`` first so a caller can name a real
    place. Otherwise globs Roblox's template autosaves: their filenames carry a
    rotating suffix and Roblox prunes them, so pinning an exact name is fragile.
    """
    override = os.environ.get(BASEPLATE_ENV, "").strip()
    if override:
        return os.path.abspath(override) if os.path.isfile(override) else None
    rows = list_place_candidates()
    return rows[0]["path"] if rows else None


#: ``Template_<placeId>_AutoRecovery_<n>[_<stamp>].rbxl`` - Roblox's template
#: autosave naming, which carries the place id in the filename. That is the place
#: a template *is*, and it is what a URI launch needs: a local file has no place
#: id, so without this the only way to open a template by id is to hardcode one.
_TEMPLATE_NAME_RE = re.compile(r"^Template_(\d+)_AutoRecovery_")


def template_place_id(path: str) -> Optional[int]:
    """The place id a Roblox template autosave belongs to, or ``None``.

    Measured: the baseplate this machine discovers is
    ``Template_95206881_AutoRecovery_4_20260930_135756.rbxl``, so the template is
    place ``95206881``. With :func:`build_launch_uri` fixed to ``universeId:0``,
    that id plus 0 is the working way to open the template by URI.
    """
    match = _TEMPLATE_NAME_RE.match(os.path.basename(path))
    return int(match.group(1)) if match else None


def list_place_candidates() -> List[Dict[str, Any]]:
    """Places the caller could choose to open, so it does not have to guess.

    The agent picks; this only enumerates. Roblox's template autosaves are
    included because they are the usual throwaway choice, but they are recovery
    copies rather than a template meant for reuse, which is why each row says
    what it is.

    Each row also carries ``place_id``, taken from the filename, so a template can
    be opened by id through :func:`build_launch_uri` without anyone hardcoding
    one. It is ``None`` for a file that is not a Roblox template autosave.
    """
    rows: List[Dict[str, Any]] = []
    for folder in _autosave_dirs():
        if not os.path.isdir(folder):
            continue
        for path in glob.glob(os.path.join(folder, "Template_*_AutoRecovery_*.rbxl")):
            try:
                stat = os.stat(path)
            except OSError:
                continue
            rows.append({
                "path": path,
                "place_id": template_place_id(path),
                "bytes": stat.st_size,
                "modified": stat.st_mtime,
                "kind": "roblox template autosave (recovery copy)",
            })
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return rows


#: Prefix/suffix for temp copies made by :func:`make_throwaway_place`. The guard
#: in :func:`cleanup_throwaway_place` is built from these, so a cleanup call
#: can never remove a file this project did not make.
THROWAWAY_PREFIX = "roblox-studio-"
THROWAWAY_SUFFIX = ".rbxl"


def make_throwaway_place() -> str:
    """Copy a discovered baseplate to a temp file and return the copy.

    A copy, never the original, so a session can be modified without touching
    anything the user owns or anything Roblox prunes on its own schedule. The
    caller then passes the returned path to :func:`launch_instance` explicitly.

    **The file is a single ``mkstemp`` entry, not a member of a named folder.**
    This used to write into a hardcoded ``<temp>/robloxstudio-mcp-baseplates``
    directory, which was wrong twice over: the name was a constant this project
    had no business claiming inside a shared system temp directory, and building
    it with ``os.makedirs`` meant a stale directory could outlive the process that
    made it. ``tempfile`` already solves both - it picks the platform's own temp
    location, and guarantees the name is unique rather than rolling a uuid by
    hand.

    The caller owns the copy: Studio holds the file open while it runs, so
    nothing here deletes it automatically. Deleting at exit would need a
    lifetime rule this module cannot observe (Studio's exit), and on Windows
    the delete fails while Studio holds the file anyway. When done, remove it
    with :func:`cleanup_throwaway_place`; copies older than a threshold can be
    swept with :func:`prune_stale_throwaway_places`.
    """
    source = find_baseplate()
    if not source:
        raise FileNotFoundError(
            "no baseplate found. Searched: " + ", ".join(_autosave_dirs())
        )
    handle, target = tempfile.mkstemp(
        prefix=THROWAWAY_PREFIX, suffix=THROWAWAY_SUFFIX
    )
    os.close(handle)
    shutil.copyfile(source, target)
    return target


def cleanup_throwaway_place(path: str) -> bool:
    """Remove one copy made by :func:`make_throwaway_place`.

    Returns True when the file was removed, False when it was kept: a name
    this project did not make, a file outside the platform temp directory, a
    file already gone, or a file Studio still holds open (on Windows the
    remove then fails). Never raises for those cases, and never removes
    anything but a ``roblox-studio-*.rbxl`` entry in the temp directory.
    """
    try:
        candidate = os.path.abspath(path)
    except (OSError, ValueError):
        return False
    name = os.path.basename(candidate)
    if not (name.startswith(THROWAWAY_PREFIX)
            and name.endswith(THROWAWAY_SUFFIX)):
        return False
    try:
        tmpdir = os.path.abspath(tempfile.gettempdir())
    except OSError:
        return False
    if os.path.dirname(candidate) != tmpdir:
        return False
    try:
        os.remove(candidate)
    except OSError:
        return False
    return True


def prune_stale_throwaway_places(
    max_age_seconds: float = 7 * 24 * 3600,
) -> Dict[str, int]:
    """Remove temp copies older than ``max_age_seconds`` (default 7 days).

    The age gate is the lifetime rule :func:`make_throwaway_place` cannot
    observe directly: a copy younger than the threshold may still belong to a
    running Studio, so only older ones are removed. Returns counts as
    ``{"scanned": n, "removed": n, "failed": n}``; ``failed`` covers files
    that vanished mid-sweep or are still held open.
    """
    try:
        tmpdir = os.path.abspath(tempfile.gettempdir())
    except OSError:
        return {"scanned": 0, "removed": 0, "failed": 0}
    try:
        names = os.listdir(tmpdir)
    except OSError:
        return {"scanned": 0, "removed": 0, "failed": 0}
    now = time.time()
    scanned = removed = failed = 0
    for name in names:
        if not (name.startswith(THROWAWAY_PREFIX)
                and name.endswith(THROWAWAY_SUFFIX)):
            continue
        scanned += 1
        full = os.path.join(tmpdir, name)
        try:
            if now - os.stat(full).st_mtime < max_age_seconds:
                continue
        except OSError:
            failed += 1
            continue
        if cleanup_throwaway_place(full):
            removed += 1
        else:
            failed += 1
    return {"scanned": scanned, "removed": removed, "failed": failed}


# --------------------------------------------------------------------------- #
# Launch
# --------------------------------------------------------------------------- #

async def launch_instance(
    place_path: str,
    *,
    studio_client: Optional[RobloxStudio] = None,
    allow_console_write: bool = False,
) -> Dict[str, Any]:
    """Start a Studio on ``place_path`` and wait for it to join the mesh.

    **One required argument.** The caller chooses what gets opened: no default
    place, no sentinel, and no second route by id. An agent that launches a
    Studio should know what it opened rather than discover a baseplate later.

    The wait budget is :data:`LAUNCH_WAIT`, not a parameter. How long Studio
    takes to open a place is a property of Studio, not of what the caller wants,
    and exposing it as an argument only invited callers to pick a value that
    either gave up early or blew the client timeout.

    A place is mandatory in practice: without one the process starts, attaches
    to nothing, and reports ``name: null``, so waiting would burn the whole
    budget with nothing to show.

    **No ``-universeId`` here.** Adding it looked like making the two routes
    consistent and was measured: with ``-universeId 0`` appended, the process
    started and signed in, then failed to open the place at all -
    ``Cannot open place file for reading.: iostream stream error``, mesh name
    ``null``, so nothing was addressable. The flag is for the place-id route only,
    where File > New uses it and where it is what makes the fetch succeed.

    ``allow_console_write`` defaults to **False**, and this is a change of
    default, not a new capability. The old code sprayed a join token into every
    attached Studio whenever it could not identify the new one by name, with no
    way for the caller to decline: measured 2026-10-01, launching one Studio wrote
    two tokens into an unrelated Studio the user had not put in scope. The
    before/after mesh diff added to :func:`_identify_launched` makes that path
    unnecessary in the normal case, so the spray is now a last resort the caller
    authorises. Declining reports the launch as **successful but unaddressed**,
    never as a failed launch, because the process really did start and a
    "retry" would orphan it.
    """
    resolved = os.path.abspath(place_path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"place not found: {resolved}")
    exe = find_studio_exe()
    workdir = os.path.dirname(exe)
    before = {p["pid"] for p in list_studio_processes()}
    # Set when a new process appeared but could not be identified, so the timeout
    # branch can say which of the two failures happened. Declared here because the
    # loop may never run, and Python would otherwise treat it as possibly-unbound.
    unattached: Optional[str] = None

    # The client is connected BEFORE the spawn, and the mesh snapshot taken here,
    # because the snapshot is only evidence if it predates the thing it is
    # evidence about. Taken after the spawn it would already contain the new
    # Studio, and the diff below would be empty for the one process it exists to
    # find. Measured 2026-10-01: taking it after meant the fallback sprayed a join
    # token into every attached Studio - including one the user had ruled
    # read-only.
    client = studio_client or await RobloxStudio.connect()
    owns = studio_client is None
    try:
        before_ids = await _mesh_ids(client)

        proc = subprocess.Popen(
            [exe, "--task", "EditFile", "--localPlaceFile", resolved],
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + LAUNCH_WAIT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return {
                    "launched": False,
                    "pid": proc.pid,
                    "exit_code": proc.returncode,
                    "error": (
                        "Studio exited during launch. Exit code "
                        f"{proc.returncode} usually means the install was being "
                        "updated underneath the launch; retry once it settles."
                    ),
                }
            # Attachment is not needed to spot our own process, and looking it up
            # costs 9.5s against 2.1s - which is most of this loop's budget. The
            # cache is bypassed with max_age=0 because the whole question is
            # whether a pid that did not exist a moment ago exists now, and a
            # cached answer would answer "no" to precisely the process we launched.
            new_pids = {
                p["pid"]
                for p in list_studio_processes(with_attachment=False, max_age=0.0)
            } - before
            if new_pids:
                # Identify the new process by its OWN pid, never by picking the
                # first row the mesh happens to list. Measured: doing the latter
                # returned the pre-existing instance's id, because a freshly
                # launched Studio reports the previous place name until it
                # finishes opening the file.
                target_pid = sorted(new_pids)[0]
                # Named `identified`, NOT `resolved`. `resolved` already holds the
                # absolute place path, and overwriting it with the result dict made
                # every launch report
                #   TypeError: expected str, bytes or os.PathLike object, not dict
                # *after* the Studio had already started and attached. The tool
                # therefore always reported failure on a launch that had in fact
                # succeeded, leaving an orphan process behind - the worst possible
                # shape for a bug, because the side effect is invisible in the
                # error.
                identified = await _identify_launched(
                    client,
                    target_pid,
                    os.path.basename(resolved),
                    before_ids=before_ids,
                    allow_console_write=allow_console_write,
                )
                if identified.get("studio_id"):
                    answer = {
                        "launched": True,
                        "pid": target_pid,
                        "studio_id": identified["studio_id"],
                        "opened": os.path.basename(resolved),
                        "mesh_name": identified.get("mesh_name"),
                        "note": (
                            "studio_id is re-minted on every restart, so treat it "
                            "as an address for this session only."
                        ),
                    }
                    if identified.get("console_writes"):
                        answer["console_writes"] = identified["console_writes"]
                    return answer
                # A new process existing is NOT the same as its Studio being on the
                # mesh. The poll below spots the process host-side within a second
                # or two, while the Studio can take much longer to attach - and
                # until it does, `list_studios` cannot contain it, so the diff has
                # nothing to find and the log route has no name to match.
                #
                # So a miss here is a *retry*, not an answer. Measured live
                # 2026-10-01: returning on the first miss reported
                # `studio_id: null` for two launches that attached moments later.
                # The unresolved report belongs at the deadline, below.
                unattached = identified.get("how")

            time.sleep(2.0)
        return {
            "launched": False,
            "pid": proc.pid,
            "error": (
                f"Studio {proc.pid} started but did not attach to the mesh with a "
                "place open within "
                f"{LAUNCH_WAIT:.0f}s. Check that MCP is enabled in Studio's "
                "Assistant settings."
            ),
            # Carried when a process appeared but could never be identified, so
            # "the process is running and unattached" is distinguishable from
            # "nothing started". The first is recoverable by hand; the second is
            # not, and the two need different advice.
            **(
                {
                    "started_but_unidentified": True,
                    "identified_by": unattached,
                    "hint": (
                        "The process is running. Re-run list_roblox_studios and match "
                        "on the place name, or read the PID's own log. Do NOT launch "
                        "again - that orphans this one."
                    ),
                }
                if unattached
                else {}
            ),
        }
    finally:
        if owns:
            await client.close()


async def _mesh_ids(client: RobloxStudio) -> Optional[Set[str]]:
    """The ``studio_id``s attached right now, or ``None`` if that could not be read.

    ``None`` and ``set()`` are deliberately different, and collapsing them is the
    bug this shape avoids. An empty set means "nothing is attached"; ``None``
    means "I could not ask". A failed read returned as ``set()`` would make every
    attached Studio look newly-arrived, so the diff in
    :func:`_identify_launched` would return the first row - which is picking,
    the thing this whole function refuses to do.
    """
    try:
        rows = await client.list_studios()
    except Exception:  # noqa: BLE001 - an unreadable mesh is not a launch failure
        return None
    rows = rows if isinstance(rows, list) else getattr(rows, "studios", [])
    found: Set[str] = set()
    for row in rows:
        sid = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        if sid:
            found.add(str(sid))
    return found


async def _identify_launched(
    client: RobloxStudio,
    pid: int,
    expected_place: str,
    *,
    before_ids: Optional[Set[str]] = None,
    allow_console_write: bool = False,
) -> Dict[str, Any]:
    """Find the mesh entry for a process we just started.

    Three routes, cheapest and least invasive first. The order is the fix.

    1. **The before/after mesh diff.** ``before_ids`` is the set of ``studio_id``s
       attached *before* the spawn; a row whose id is not in it is new. This is
       the route that needs no log read, no console write, and no name match -
       so it is the only one that works when two Studios share a place name,
       which is exactly the case the old code fell through to spraying a token.
       Measured 2026-10-01 on two identical sessions: the name route cannot
       separate them, and the diff separates them for free.
    2. **The new process's own log command line**, compared against the mesh name.
       Costs one log read and writes nothing. Reached when the diff yields
       nothing usable - a pre-existing Studio that attached between the snapshot
       and the spawn, or no snapshot at all.
    3. **A console token per candidate.** Last resort, opt-in, and now counted.

    ``before_ids`` is ``None`` - not ``set()`` - when the mesh could not be read
    before the spawn, because an empty set would make every attached Studio look
    new. See :func:`_mesh_ids`.
    """
    studios = await client.list_studios()
    rows = studios if isinstance(studios, list) else getattr(studios, "studios", [])

    def _sid(row: Any) -> Optional[str]:
        raw = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        return str(raw) if raw else None

    def _name(row: Any) -> Optional[str]:
        return row.get("name") if isinstance(row, dict) else getattr(row, "name", None)

    # Route 1: the diff. Skipped entirely when there is no snapshot.
    if before_ids is not None:
        arrived = [row for row in rows if _sid(row) and _sid(row) not in before_ids]
        if len(arrived) == 1:
            row = arrived[0]
            return {
                "studio_id": _sid(row),
                "mesh_name": _name(row),
                "how": "studio_id absent from the mesh before this launch and present after",
                "console_writes": 0,
            }
        if len(arrived) > 1:
            # Something else attached at the same time - another agent, most
            # likely. That is genuinely ambiguous and the answer is to say so.
            return {
                "studio_id": None,
                "mesh_name": None,
                "how": "more than one Studio attached during this launch",
                "why": (
                    "%d studios appeared on the mesh during the launch, so the new "
                    "one cannot be singled out. They are: %s. Re-run "
                    "list_roblox_studios and pick by place name." % (
                        len(arrived),
                        ", ".join(
                            "%s (%s)" % (_name(r) or "no name", _sid(r)) for r in arrived
                        ),
                    )
                ),
                "console_writes": 0,
                "candidates": [
                    {"studio_id": _sid(r), "name": _name(r)} for r in arrived
                ],
            }

    identity = logid.live_identities([pid]).get(pid)
    if identity:
        matches = []
        for row in rows:
            sid = _sid(row)
            if sid and logid.name_matches_identity(_name(row), identity):
                matches.append((sid, _name(row)))
        if len(matches) == 1:
            sid, name = matches[0]
            return {
                "studio_id": sid,
                "mesh_name": name,
                "how": "the new process's own log command line, no console write",
                "task": identity.get("task"),
                "console_writes": 0,
            }

    # Route 3. Gated, and counted.
    #
    # This used to walk `rows` in mesh order and print a join token into **every**
    # Studio until one matched - one mesh round trip and one user-visible console
    # line each, in Studios the caller never named, to answer a question routes 1
    # and 2 had already failed to answer. Measured 2026-10-01: two tokens landed
    # in a Studio the user had ruled read-only, because the launch named only the
    # file to open and not which Studio to ask.
    #
    # It stays available, because the two routes above genuinely cannot decide
    # every case. It is no longer the default, and the caller authorises it.
    #
    # `allow_console_write` is therefore a statement about scope, not a licence:
    # it declines to touch Studios the caller never named, and passing it says
    # only that this launch may. It does not grant anything on any other
    # instance, and it is never a reason to name one implicitly - see
    # `AGENTS.md` rule 1, which is about the instance rather than the flag.
    if not allow_console_write:
        return {
            "studio_id": None,
            "mesh_name": None,
            "how": "not identified, and no console write was authorised",
            "why": (
                "The new Studio attached, but neither the before/after mesh diff nor "
                "its own log command line singled it out. Re-run list_roblox_studios "
                "and match on the place name, or pass allow_console_write to have one "
                "token printed per candidate."
            ),
            "console_writes": 0,
        }

    ranked = sorted(
        (row for row in rows if _sid(row)),
        key=lambda row: not (
            identity and logid.name_matches_identity(_name(row), identity)
        ),
    )
    writes = 0
    for row in ranked:
        sid = _sid(row)
        if not sid:
            continue
        writes += 1
        found = await _resolve_by_console_token(client, sid, [{"pid": pid}])
        if found.get("resolved") and found.get("pid") == pid:
            return {
                "studio_id": sid,
                "mesh_name": _name(row),
                "how": found.get("how"),
                "console_writes": writes,
            }
    return {
        "studio_id": None,
        "mesh_name": None,
        "how": "no mesh entry for the new process yet",
        "why": (
            "Printed a join token to %d candidate Studio(s) and none answered with "
            "this process's id." % writes
        ),
        "console_writes": writes,
    }





# --------------------------------------------------------------------------- #
# Resolve studio_id -> pid
# --------------------------------------------------------------------------- #

#: Stand-in for a refusal that reached the caller with no explanation. Its whole
#: purpose is to name the specific confusion it prevents: ``resolved: False`` with
#: no reason reads as "no such Studio", and the caller's next move is to give up
#: or to pick a candidate, both of which are wrong.
_UNEXPLAINED_REFUSAL = (
    "Refused without a reason: %d candidate process(es) and the log chain could not "
    "decide between them. This is unresolved, NOT 'not found' - do not pick a "
    "candidate and do not report the Studio as missing."
)


def _with_reason(answer: Dict[str, Any], reason: Optional[str]) -> Dict[str, Any]:
    """Guarantee a refusal carries a reason, mutating and returning ``answer``.

    Applied at every ``resolved: False`` exit rather than trusted to
    :func:`logid.ambiguous_reason`, because the failure this prevents is silent by
    construction: a resolver that correctly declines to guess is *right*, so
    nothing crashes and nothing fails a test unless someone reads the field. That
    is how a ``None`` reason survived a live measurement of this exact path.
    """
    if not answer.get("resolved") and not (answer.get("error") or reason):
        answer["error"] = _UNEXPLAINED_REFUSAL % len(answer.get("candidates") or [])
    elif not answer.get("resolved") and not answer.get("error") and reason:
        answer["error"] = reason
    return answer


async def resolve_pid_for_studio(
    client: RobloxStudio,
    studio_id: str,
    allow_console_write: bool,
) -> Dict[str, Any]:
    """Find the OS process backing a ``studio_id``.

    **A name match alone is not sufficient, and using one killed the wrong
    Studio.** Measured: asked to stop the instance launched from
    ``Baseplate-788951205.rbxl``, this resolved to a *different* process and
    reported ``resolved_by: "place-name match"``. Two reasons the name is
    untrustworthy:

    * the mesh ``name`` is not the file that was opened. After launching
      ``Baseplate-788951205.rbxl`` the mesh still reported
      ``Baseplate-1340472086.rbxl``, an older instance, so the match pointed at
      the pre-existing Studio.
    * two Studios on one place have identical names, and a server and its
      clients all report ``name: null``.

    So the name is never trusted on its own. It is resolved **through each
    candidate process's own log**, which states both its PID and its launch
    command line, and that pairing is what gets reported. Nothing is printed to
    the console unless the log genuinely cannot decide, and then only with the
    caller's permission.

    A row with no name at all takes a different path, because the chain above
    cannot start: a play test's server and clients report ``name: null``, so there
    is nothing to match and nothing that could stand in for it. Those go to
    :func:`_resolve_unnamed_studio`, which follows the ``-parentPid`` each child
    states in its own command line.

    ``allow_console_write`` is required rather than defaulted because that last
    resort *prints a line to the console*, a visible side effect on the user's
    session that the caller should authorise.
    """
    studios = await client.list_studios()
    rows = studios if isinstance(studios, list) else getattr(studios, "studios", [])
    name = None
    found_id = False
    for row in rows:
        rid = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        if rid == studio_id:
            found_id = True
            name = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
            break
    if not found_id:
        return {"resolved": False,
                "error": f"no connected Studio has id {studio_id!r}"}

    # Every process, not just attached ones: an unattached match is exactly the
    # kind of near-miss that produced the wrong kill. Attachment is not fetched
    # here - nothing in this function joins on it, and fetching it costs 9.5s
    # against 2.1s for the list itself.
    all_processes = list_studio_processes(with_attachment=False)
    # The fallback pool is every live Studio process, not only the attached ones.
    # Restricting it to attached was both wrong - a process that has not finished
    # attaching is still a candidate, and it is exactly the newly launched one -
    # and impossible here, since attachment is not fetched.
    attached = all_processes
    # The creation times let the log sweep skip the thousands of logs belonging to
    # Studios that exited months ago, which is the normal state of a long-lived
    # machine. It only narrows which files are read; the PID in the log decides.
    started = {
        p["pid"]: logid.parse_process_started(p.get("created"))
        for p in all_processes
    }
    started = {pid: when for pid, when in started.items() if when is not None}
    identities = logid.live_identities([p["pid"] for p in all_processes], started)

    # A play test's server and clients report ``name: null``, so the chain below
    # cannot start: there is no name to compare against anything. It is not a
    # missing value either - a session member opens no document of its own, so
    # ``null`` is the truth and no other log field will supply a substitute. The
    # ``-parentPid`` each child states in its own command line is the one edge
    # that reaches them, and it is tried before the console token because it costs
    # nothing and because a pool narrowed to the play-test's own processes is a
    # much smaller thing for the token to have to guess between.
    if not name:
        return await _resolve_unnamed_studio(
            client, studio_id, all_processes, identities, rows, allow_console_write
        )

    # The mesh name is a property of the mesh, not of a process. Matching it
    # against the log's own record of how that process was launched is what makes
    # it evidence rather than a guess.
    candidates = logid.match_mesh_name(name, identities)
    if len(candidates) == 1:
        pid = candidates[0]
        identity = identities[pid]
        return {
            "resolved": True,
            "pid": pid,
            "how": "matched the mesh name against each process's own log command line",
            "matched_on": os.path.basename(str(identity.get("place_path") or name)),
            "log": identity.get("log"),
            "task": identity.get("task"),
            # The pid came from the live process list a moment ago, and that list
            # only contains processes that exist. Saying so would be an inference
            # dressed as a measurement, so the field is omitted rather than
            # guessed, and the caller already has `pid` to check liveness with.
            "from_live_process_list": True,
        }

    # The candidate PIDs go in whole, NOT the identities they happen to have. An
    # earlier version passed the filtered identities, so a candidate whose log
    # could not be read dropped out of the count and a two-process ambiguity was
    # reported as "no ambiguity" with a reason of None.
    reason = logid.ambiguous_reason(name, candidates, identities)
    if not allow_console_write:
        return _with_reason(
            {
                "resolved": False,
                "candidates": candidates or [p["pid"] for p in attached],
                "error": reason,
                "needs_console_write": True,
            },
            reason,
        )
    pool = [{"pid": p} for p in candidates] if candidates else attached
    return await _resolve_by_console_token(client, studio_id, pool)


async def _resolve_unnamed_studio(
    client: RobloxStudio,
    studio_id: str,
    all_processes: List[Dict[str, Any]],
    identities: Dict[int, Dict[str, Any]],
    rows: Any,
    allow_console_write: bool,
) -> Dict[str, Any]:
    """Resolve a ``studio_id`` whose mesh row reports ``name: null``.

    The play-test case. A ``StartServer`` and its ``StartClient``s report no name
    because a session member opens no document of its own, so the name join has
    nothing to start from and no other log field can stand in for it.

    The edge used instead is the child's own ``-parentPid``: it names the
    launching Studio's process outright. The parent is not identified by anything
    new - it is looked up in the same identity map the whole chain runs on, which
    the log-PID join built. So a child is one hop from a process whose place is
    already known, and no console write is involved.

    ``logid.resolve_unnamed_studio`` decides, and it resolves only when one
    anchored child and one unnamed mesh row account for each other exactly. With
    a server *and* its clients live, that is not satisfied - and it must not be:
    the wrong PID here means terminating the wrong Studio, which is the exact
    failure this function's predecessor was written to stop. The unresolved
    answer still carries the play-test tree the logs did establish, and narrows
    the token fallback to those processes.
    """
    unnamed = sum(
        1
        for row in rows
        if not (row.get("name") if isinstance(row, dict) else getattr(row, "name", None))
    )
    live = {p["pid"] for p in all_processes}
    found = logid.resolve_unnamed_studio(identities, live, unnamed)

    if found["resolved"]:
        child = found["child"]
        answer: Dict[str, Any] = {
            "resolved": True,
            "pid": child["pid"],
            "how": (
                "the -parentPid in this process's own log command line, walked to "
                "the Studio that started the test"
            ),
            "task": child["task"],
            "log": child.get("log"),
            "parent_pid": child.get("parent_pid"),
            "parent_place": child.get("parent_place_path"),
            "parent_log": child.get("parent_log"),
            # The pid came out of a map keyed by the live process list, which only
            # contains processes that exist. Said plainly rather than as a guess.
            "from_live_process_list": True,
        }
        if child.get("parent_guid_confirms") is False:
            # Reported, not obeyed. Whether -parentSessionGuid is the parent
            # *process's* session guid is unverified - it is read off the flag's
            # name and one command line - so vetoing the join on a disagreement
            # would let an unverified premise disable a working edge. Loud beats
            # silent, and a caller who needs certainty has the field to check.
            answer["warning"] = (
                f"the parent log's Session GUID ({child.get('parent_session_guid')}) "
                f"differs from the -parentSessionGuid this process recorded "
                f"({child.get('stated_parent_session_guid')}), so the parent pid may "
                "be a reused pid. The join stands on the -parentPid edge alone, and "
                "that they are the same value is unverified"
            )
        return answer

    pool = [{"pid": pid} for pid in found["candidates"]] or all_processes
    unresolved: Dict[str, Any] = _with_reason(
        {
            "resolved": False,
            "candidates": found["candidates"] or sorted(live),
            "error": found["reason"],
            # What the logs *did* establish, so an unresolved answer is still a
            # diagnosis rather than a shrug.
            "playtest_tree": found["tree"],
        },
        found["reason"],
    )
    if not allow_console_write:
        unresolved["needs_console_write"] = True
        return unresolved
    return await _resolve_by_console_token(client, studio_id, pool)


async def _resolve_by_console_token(
    client: RobloxStudio, studio_id: str, candidates: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Join a ``studio_id`` to a PID by finding which process logged a token.

    This prints one short line to the console, so it is a last resort rather than
    the normal path. It is still needed for the two cases the logs cannot decide:
    several URI launches of one place, and a server or client whose mesh name is
    ``null``.

    **The target is named explicitly, and that is load-bearing.** An earlier
    version called ``execute_luau`` with no ``studio_id``, relying on inference.
    Inference refuses to pick when more than one Studio is connected - and
    ambiguity only ever arises *because* more than one Studio is connected. So the
    last-resort path failed in precisely the situation it existed to handle, and
    the error surfaced as ``could not print a join token: 2 Roblox Studio
    instances are connected``. Measured live against two Studios both reporting
    the mesh name ``Place1``.

    Once the owning log is found, the PID comes from that log's own
    ``UIThreadNotifier`` line. An earlier version instead compared the log
    filename's start stamp to each process's creation time within 5s, which was a
    guess: two launches seconds apart are exactly when that tolerance misfires.
    """
    token = f"RBXPID{uuid.uuid4().hex[:10].upper()}"
    try:
        await client.execute_luau(f'print("{token}")', studio_id=studio_id)
    except Exception as exc:  # noqa: BLE001
        return {"resolved": False, "error": f"could not print a join token: {exc}"}

    directory = logid.log_dir()
    owner_log: Optional[str] = None
    if os.path.isdir(directory):
        for entry in sorted(os.listdir(directory), reverse=True):
            if logid.LOG_STAMP_RE.search(entry) and entry.endswith(".log"):
                path = os.path.join(directory, entry)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                        if token in handle.read():
                            owner_log = entry
                            break
                except OSError:
                    continue
    if owner_log is None:
        return {"resolved": False, "error": "join token did not appear in any Studio log"}

    # Narrow to the pool the caller believes is in play, so a token cannot resolve
    # to a process that was already excluded.
    allowed = {p["pid"] for p in candidates} if candidates else None

    identity = logid.read_identity(os.path.join(directory, owner_log))
    pid = identity.get("pid") if identity else None
    if pid is not None and identity is not None and (allowed is None or pid in allowed):
        return {
            "resolved": True,
            "pid": pid,
            "how": "console token in a log that states its own pid",
            "log": owner_log,
            "task": identity.get("task"),
        }

    # The owning log names a pid that is not live, or is outside the pool. Say so
    # precisely rather than falling back to a timestamp guess.
    if pid is None:
        detail = f"log {owner_log!r} does not state a pid"
    else:
        detail = (
            f"log {owner_log!r} belongs to pid {pid}, which is "
            + ("not one of the candidate processes" if allowed is not None
               else "not currently running")
        )
    return {
        "resolved": False,
        "error": detail,
        "candidates": sorted(allowed) if allowed is not None else None,
    }


# --------------------------------------------------------------------------- #
# Stop
# --------------------------------------------------------------------------- #

def terminate_process(pid: int, *, grace_seconds: float = 8.0) -> Dict[str, Any]:
    """Terminate a process and confirm it went away.

    The single place this module kills anything, so the blast radius is
    auditable: a PID must be resolved from a ``studio_id`` first, never guessed.
    """
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=30)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return {"stopped": True, "pid": pid}
        time.sleep(0.5)
    return {"stopped": not _pid_alive(pid), "pid": pid,
            "error": "process did not exit within the grace period"}


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is still running. Windows uses CIM, macOS uses ``kill -0``.

    The two are not equivalent in strictness - ``kill -0`` succeeds for a
    process the caller does not own - but for a process this module launched it is
    the standard check and the only portable one.
    """
    return platform.pid_alive(pid)
