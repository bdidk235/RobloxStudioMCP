"""Host-side registry of connected Studio instances.

Why this exists
---------------
``list_roblox_studios`` is the only discovery tool the official Studio MCP
offers, and its ``studio_id`` is **not stable**: it is minted by the proxy
process and changes on every Studio restart. That makes it a transport token,
not an identity, and it is the reason this project cannot answer questions
like "is the Studio I launched still the one I launched?".

Roblox's documentation frames the explicit id as deliberate — agents address
instances "explicitly instead of relying on session state" — so there is no
documented in-band path to the id, and none exists in practice:

* ``game.UniqueId`` is unreadable under ``execute_luau``; the thread lacks the
  ``RobloxScript`` capability ("The current thread cannot read 'UniqueId'").
  ``ReflectionService`` does not list it either, since it is capability-gated
  out of the class registry.
* ``game.JobId`` is empty outside a live session.
* ``game.Parent`` is ``None``.

``game:GetDebugId()`` *is* readable, and is the usable substitute: it is stable
across repeated calls, differs per Studio instance, and identifies the
DataModel root. Two limits, both load-bearing, and both measured:

* It is per **DataModel root**, not per process. A play session reports a
  different value than Edit mode does, so this must be read from **Edit**; a
  Server-side read will not match and must not be compared against an Edit read.
* It does **not** survive a Studio restart. Measured, same place file, same
  machine: ``0_186696`` before a restart, ``0_186502`` after. It is also per
  *instance*, not per process, since a child ``Folder`` returns a different
  value. So it is a session-scoped discriminator and nothing more.

That second limit is load-bearing for the whole design, so it is worth being
blunt about the consequence: **a Studio instance cannot be tracked across a
restart, because a restart genuinely creates a new instance.** There is no
durable in-band identifier to carry across, and none was found. Probed and
ruled out: ``game.PlaceId`` and ``game.GameId`` are both ``0`` for an
unpublished place, ``game.JobId`` is empty outside a session, and there is no
process-id API reachable from Luau at all (no ``os.getpid``, no
``ProcessService``, no ``game.ProcessId``, no ``DiagnosticsService``, and
``settings()`` does not exist under ``execute_luau``).

So identity comes in two tiers, and conflating them is the mistake:

* **Session tier**, valid only while the process lives: the proxy's
  ``studio_id`` addresses an instance, and ``GetDebugId`` separates two
  instances of the *same* place. Neither is durable.
* **Place tier**, durable but coarse: the place file path, or ``game.Name``.
  It survives restarts, and it cannot tell two windows of one place apart.

What is available to bridge them is host-side, measured: the mesh is a
WebSocket on ``127.0.0.1:13469``, so ``Get-NetTCPConnection -RemotePort 13469``
returns the attached Studio processes **by PID** with no name ambiguity, and
each process writes its own timestamped log under ``%LOCALAPPDATA%\\Roblox\\logs``
whose name encodes that process's start time. Printing a unique token and
finding the log that contains it therefore joins a ``studio_id`` to a PID even
when two Studios share a place name, which nothing else available does.

This module keeps the join **host-side**, in this process's own state directory.
Nothing is written into the DataModel, so a registry entry can never reach the
place file, a published place, or a team create.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

from ..roblox import RobloxStudio
from ..types import CallToolResult

#: Bumped when the on-disk shape changes; older files are ignored, not deleted.
REGISTRY_VERSION = 1

#: Entries not seen for this long are reported as stale rather than dropped,
#: so a closed Studio stays discoverable as "was here" without looking live.
STALE_AFTER_SECONDS = 24 * 60 * 60

#: Entries unseen for this long are dropped on the next record, bounding growth.
#: Every restart orphans its old entry permanently - a new debug_id means a new
#: key, and the old key can never match again - so without pruning the file
#: grows by one entry per restart forever (measured 7 stored for 2 live). Thirty
#: days keeps the "was here" history that resolve() is for while dropping what
#: no caller will ever ask about. The just-recorded entry is exempt, so clock
#: skew can only spare entries, never delete the one just written.
EXPIRE_AFTER_SECONDS = 30 * 24 * 60 * 60

#: The identity fields, built once so the two channels cannot drift.
_IDENTITY_EXPR = """{
    game:GetDebugId(),
    tostring(game.Name),
    tostring(game.PlaceId),
    tostring(game.GameId),
}"""

#: **Primary channel: the return value.** One tool call, and no console write.
#:
#: The return channel works on the current build — measured, and recorded in
#: `TODO.md`: an earlier note here said "inline execute_luau drops a script's
#: return value" and that belief outlived its own refutation, keeping a console
#: round trip alive for nothing. It was not free even when it was correct:
#: console reads are the fragile channel (a measured case had the console
#: reporting **0** `MCPID` lines while the log held one), and this project has
#: otherwise gone out of its way to avoid user-visible console writes.
#:
#: Returns a **concatenated string**, never a table: a table arrives with its
#: integer keys stringified (`{"1":…}`) and a string has no keys to lose. The
#: payload is a few bytes against a 100,015-character limit.
_READ_IDENTITY_RETURN_LUAU = (
    'return "MCPID\\t" .. table.concat(%s, "\\t")' % _IDENTITY_EXPR
)

#: **Fallback only: print plus a tagged console read.** Used when the return
#: channel yields nothing usable. `print` is not the primary because it writes
#: into a user-visible buffer and leaves a tag behind; it is the fallback
#: because it is the only channel known to work on every build seen.
_READ_IDENTITY_PRINT_LUAU = 'print("MCPID\\t" .. table.concat(%s, "\\t"))' % _IDENTITY_EXPR

_IDENTITY_PREFIX = "MCPID\t"


def _parse_identity(text: str) -> Optional[InBandIdentity]:
    """Pull the four fields out of a tagged line, or ``None`` if it is not one.

    Shared by both channels deliberately. The only thing that differs between
    returning and printing is how the text arrives; the parse is identical, so
    two copies of it would be two things to keep in step.
    """
    for raw in text.splitlines():
        line = raw.strip().strip('"')
        start = line.find(_IDENTITY_PREFIX)
        if start < 0:
            continue
        fields = line[start + len(_IDENTITY_PREFIX):].split("\t")
        if len(fields) < 4:
            continue
        debug_id, name, place_id, game_id = fields[0], fields[1], fields[2], fields[3]
        return {
            "debug_id": debug_id,
            "name": name,
            # Non-numeric is recorded as 0 rather than propagated: an
            # unpublished place really does report 0, and this channel cannot
            # tell "unpublished" from "unparseable".
            "place_id": int(place_id) if place_id.isdigit() else 0,
            "game_id": int(game_id) if game_id.isdigit() else 0,
        }
    return None


class InBandIdentity(TypedDict):
    """The four fields :func:`read_in_band_identity` reads.

    ``place_id`` and ``game_id`` are ``int``, never the raw text: the Luau sends
    ``tostring(game.PlaceId)`` and a non-numeric field is recorded as ``0``
    rather than propagated. Keeping the type honest here is what stops a caller
    from treating the digit check as a validity signal it is not.
    """

    debug_id: str
    name: str
    place_id: int
    game_id: int


class RegistryEntry(TypedDict):
    """One stored instance, as :func:`record` writes and :func:`resolve` returns.

    ``debug_id``/``name``/``place_id``/``game_id`` are ``None`` when no identity
    was read. That is the normal case for a Studio that could not be reached, so
    they are nullable rather than absent: the key's presence says "we tried",
    which is different from "we have no idea".

    :func:`resolve` adds ``stale`` and ``age_seconds`` to a copy of this on the
    way out; those are not stored and so are not declared here.
    """

    debug_id: Optional[str]
    name: Optional[str]
    place_id: Optional[int]
    game_id: Optional[int]
    last_studio_id: str
    last_seen: float
    studio_id_history: List[str]
    id_changed: bool


class ResolvedEntry(RegistryEntry):
    """:class:`RegistryEntry` plus what :func:`resolve` computes on the way out.

    ``stale`` is a flag and never a filter — a closed Studio stays discoverable
    as "was here", which is the whole reason the field exists rather than the
    entry being dropped.
    """

    stale: bool
    age_seconds: float


class ListedInstance(TypedDict):
    """One row of :func:`list_instances`: the proxy list joined with identity.

    This is the join of two sources that can each be missing, so nearly every
    field is nullable. ``identity_error`` is what makes that legible: it is
    ``None`` when the read succeeded *or* was skipped, and set to the failure
    text otherwise, so "no debug_id" is distinguishable from "we never looked".

    ``reported_name`` and ``place_id`` are ``Any`` and that is a deliberate,
    narrow admission rather than laziness. Both can come from the proxy's
    ``list_roblox_studios`` payload, which is unvalidated JSON read straight off
    the wire: ``reported_name`` is whatever ``name`` holds, and ``place_id``
    falls back to the proxy's ``placeId`` when no identity was read. Declaring
    them ``str``/``int`` would be a claim about a third-party payload that
    nothing in this project checks. Every other field is either produced here or
    comes from :class:`InBandIdentity`, which is parsed and typed.
    """

    studio_id: str
    reported_name: Any
    debug_id: Optional[str]
    place_id: Any
    game_id: Optional[int]
    identity_error: Optional[str]
    registered: bool
    id_changed: bool


class ListResult(TypedDict):
    """The envelope :func:`list_instances` returns."""

    version: int
    registry_path: str
    count: int
    instances: List[ListedInstance]
    registered_total: int


class ResolveOk(TypedDict):
    """Exactly one instance matched."""

    status: Literal["ok"]
    match: ResolvedEntry


class ResolveNotFound(TypedDict):
    """Nothing matched. ``known`` lists everything registered, so a caller that
    guessed a selector wrong can see the right one without a second call."""

    status: Literal["not_found"]
    reason: str
    known: List[ResolvedEntry]


class ResolveAmbiguous(TypedDict):
    """Several matched, so no selector was applied. ``resolve`` refuses to guess
    and hands back every candidate instead."""

    status: Literal["ambiguous"]
    reason: str
    candidates: List[ResolvedEntry]


#: The three shapes :func:`resolve` returns.
#:
#: A ``Literal``-discriminated union rather than one dict with optional keys.
#: The arms carry disjoint keys, so a single shape would make ``result["match"]``
#: type-check against a ``not_found`` reply that has no such key — the same
#: class of mistake as the ``WatchResult`` bug this project shipped. A caller
#: has to branch on ``status`` first, which is the only correct way to read it.
ResolveResult = Union[ResolveOk, ResolveNotFound, ResolveAmbiguous]


def registry_path() -> str:
    """Return the registry file path for this host.

    ``ROBLOX_STUDIO_MCP_REGISTRY`` overrides it, which is what the tests use.
    """
    override = os.environ.get("ROBLOX_STUDIO_MCP_REGISTRY")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
    return os.path.join(base, "roblox-studio-mcp", "studios.json")


def _load(path: str) -> Dict[str, Any]:
    """Read the registry file, or return an empty one.

    Stays ``Dict[str, Any]`` on purpose. This parses a file on disk that
    anything can have written — another tool version, a hand edit, a half-write
    from another process — and the checks below establish only that the top level
    is a dict at the current version with a dict of instances. The *entries* are
    not validated here, so claiming :class:`RegistryEntry` would be a promise the
    function does not keep. ``record`` is where an entry's shape is established.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"version": REGISTRY_VERSION, "instances": {}}
    if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
        # A file from a different version is left alone: another tool version
        # may own it, and deleting it would destroy their state.
        return {"version": REGISTRY_VERSION, "instances": {}}
    if not isinstance(data.get("instances"), dict):
        return {"version": REGISTRY_VERSION, "instances": {}}
    return data


def _store(path: str, data: Dict[str, Any]) -> None:
    """Write atomically so a crash mid-write cannot leave a truncated file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
    )
    try:
        with handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


async def read_in_band_identity(studio: RobloxStudio) -> Optional[InBandIdentity]:
    """Read the DataModel's own identity via ``GetDebugId``.

    Must run in Edit mode. A play session reports a different id for the same
    Studio, so a Server-side read is not comparable and is reported as such
    rather than being silently accepted.

    **The return channel is primary; ``print`` is the fallback.** This used to
    be console-only, because the docstring believed "execute_luau drops a
    script's return value". That was measured false on the current build and
    recorded as such, but the call site kept the old belief — so the identity
    read paid two tool calls per Studio and wrote into a user-visible buffer for
    a value the return channel already carries.

    Order, and why: try ``return`` first (one call, no console write); if it
    yields nothing parseable, fall back to ``print`` + ``get_console_output``,
    which is known to work on every build seen. The fallback is the fallback
    *because* it writes — not because it is more reliable. So a build that
    regresses the return channel still resolves, and costs a console write
    rather than an identity.
    """
    try:
        result: CallToolResult = await studio.call(
            "execute_luau",
            {"code": _READ_IDENTITY_RETURN_LUAU, "datamodel_type": "Edit"},
        )
        parsed = _parse_identity(result.text())
        if parsed is not None:
            return parsed
    except Exception:  # noqa: BLE001 - a channel failure is not a read failure
        # Fall through to the console. Reporting here would be wrong: the
        # fallback exists precisely for the case where this channel is
        # unavailable, and that is not itself a fault worth surfacing.
        pass

    await studio.call(
        "execute_luau", {"code": _READ_IDENTITY_PRINT_LUAU, "datamodel_type": "Edit"}
    )
    console: CallToolResult = await studio.call("get_console_output", {})
    return _parse_identity(console.text())


def record(
    *,
    studio_id: str,
    identity: Optional[InBandIdentity],
    path: Optional[str] = None,
) -> RegistryEntry:
    """Merge one observed instance into the registry and return the entry."""
    target = path or registry_path()
    data = _load(target)
    now = time.time()

    key = (identity or {}).get("debug_id") or f"session:{studio_id}"
    entry = data["instances"].get(key)
    if not isinstance(entry, dict):
        entry = {}

    # Keep the most recent proxy id per stable key, so a restart is visible as
    # the id changing while the entry keeps its identity. Computed before the
    # entry is built so the entry can be one complete literal: a TypedDict that
    # is filled by three separate assignments is not much of a declaration, and
    # this is the only place an entry's shape is established.
    history = entry.get("studio_id_history")
    if not isinstance(history, list):
        history = []
    if studio_id not in history:
        history.append(studio_id)
    history = history[-10:]

    # Built fresh rather than updated in place, so the returned value is this
    # module's idea of an entry and not whatever the file happened to hold. The
    # file is not validated on read (see _load), so an inherited key of unknown
    # provenance would otherwise ride along in the return value.
    merged: RegistryEntry = {
        "debug_id": (identity or {}).get("debug_id"),
        "name": (identity or {}).get("name"),
        "place_id": (identity or {}).get("place_id"),
        "game_id": (identity or {}).get("game_id"),
        "last_studio_id": studio_id,
        "last_seen": now,
        "studio_id_history": history,
        "id_changed": len(history) > 1,
    }

    data["instances"][key] = merged
    for old_key, old_entry in list(data["instances"].items()):
        if old_key == key:
            continue
        if not isinstance(old_entry, dict):
            # Not an entry this module wrote (hand edit, foreign version).
            # Drop it rather than crash on it: _load explicitly contemplates
            # both, and record() runs on every refreshing list, so a raise
            # here would break listing until hand-fixed. Mirrors Node, which
            # reads last_seen as undefined on any non-object shape.
            del data["instances"][old_key]
            continue
        try:
            seen = float(old_entry.get("last_seen", 0))
        except (TypeError, ValueError):
            seen = 0
        if now - seen > EXPIRE_AFTER_SECONDS:
            del data["instances"][old_key]
    _store(target, data)
    return merged


def load_all(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the whole registry as read from disk."""
    return _load(path or registry_path())


def resolve(
    *,
    debug_id: Optional[str] = None,
    name: Optional[str] = None,
    place_id: Optional[int] = None,
    path: Optional[str] = None,
) -> ResolveResult:
    """Look a registered instance up by its stable key.

    Refuses to guess: an ambiguous match returns every candidate so the caller
    can choose, which is the same rule the Studio-selection path uses. A stale
    entry is returned but flagged, never treated as live.
    """
    data = load_all(path)
    instances = data.get("instances", {})
    now = time.time()

    def decorate(entry: Dict[str, Any]) -> ResolvedEntry:
        # Reconstructed field by field rather than copied. This is a behaviour
        # change: keys the stored entry has that this module does not write are
        # now dropped from the reply instead of passed through. It is what makes
        # the declared type true — the stored entry is unvalidated (see _load),
        # so copying it would let a hand-edited or foreign-version key ride out
        # under a type that claims to be complete. Nothing this module writes is
        # affected; only unknown keys are.
        last_seen = float(entry.get("last_seen", 0))
        return {
            "debug_id": entry.get("debug_id"),
            "name": entry.get("name"),
            "place_id": entry.get("place_id"),
            "game_id": entry.get("game_id"),
            "last_studio_id": str(entry.get("last_studio_id", "")),
            "last_seen": last_seen,
            "studio_id_history": list(entry.get("studio_id_history") or []),
            "id_changed": bool(entry.get("id_changed", False)),
            "stale": (now - last_seen) > STALE_AFTER_SECONDS,
            "age_seconds": round(now - last_seen, 3),
        }

    if debug_id:
        hit = instances.get(debug_id)
        return {"status": "ok", "match": decorate(hit)} if isinstance(hit, dict) else {
            "status": "not_found",
            "reason": f"No registered instance with debug_id {debug_id!r}.",
            "known": [decorate(e) for e in instances.values()],
        }

    candidates = list(instances.values())
    if name is not None:
        candidates = [e for e in candidates if e.get("name") == name]
    if place_id is not None:
        candidates = [
            e for e in candidates if int(e.get("place_id") or 0) == int(place_id)
        ]
    matches = [decorate(e) for e in candidates]
    if len(matches) == 1:
        return {"status": "ok", "match": matches[0]}
    if not matches:
        return {
            "status": "not_found",
            "reason": "No registered instance matches those selectors.",
            "known": [decorate(e) for e in instances.values()],
        }
    return {
        "status": "ambiguous",
        "reason": (
            f"{len(matches)} registered instances match. Pass debug_id to pick "
            f"one; list order is not meaningful."
        ),
        "candidates": matches,
    }


async def list_instances(
    studio_client: Optional[RobloxStudio] = None,
    *,
    refresh: bool = True,
    path: Optional[str] = None,
) -> ListResult:
    """Enumerate Studio instances, merging the proxy list with the registry.

    ``studio_id`` comes from the proxy and changes on every Studio restart.
    ``debug_id`` comes from the DataModel and separates instances within a
    session, but it **also** changes on restart, so neither is durable and the
    registry does not make a restarted instance traceable by itself. What it
    does give is history: a restart shows up as a new ``studio_id`` under a
    place whose previously seen ids are still on record, so a caller can tell
    "this place restarted" from "this is a different place".
    """
    # Own the connection for the whole walk: reading each identity is a tool
    # call, and a client closed after listing would leave every read failing
    # with "Not connected" and no identity to register.
    client = studio_client or await RobloxStudio.connect()
    try:
        raw = await client.list_studios()

        entries: List[ListedInstance] = []
        for studio in raw:
            studio_id = str(
                studio.get("id") or studio.get("studio_id") or studio.get("studioId") or ""
            )
            name = studio.get("name")
            identity: Optional[InBandIdentity] = None
            error: Optional[str] = None
            if refresh and studio_id:
                scoped = RobloxStudio(client.client, studio_id)
                try:
                    identity = await read_in_band_identity(scoped)
                    if identity is None:
                        error = "GetDebugId returned no identity"
                except Exception as exc:  # noqa: BLE001 - reported, never fatal
                    error = str(exc)
            entry: ListedInstance = {
                "studio_id": studio_id,
                "reported_name": name,
                "debug_id": (identity or {}).get("debug_id"),
                # .get with a default, not or-default: an identity that read
                # place_id 0 is a real answer for an unpublished place and must
                # not be replaced by whatever the proxy claims.
                "place_id": (identity or {}).get("place_id", studio.get("placeId")),
                "game_id": (identity or {}).get("game_id"),
                "identity_error": error,
                # Both branches set both keys, so the row has one shape rather
                # than a shape that depends on whether a refresh happened.
                "registered": bool(refresh and studio_id),
                "id_changed": False,
            }
            if refresh and studio_id:
                recorded = record(studio_id=studio_id, identity=identity, path=path)
                entry["id_changed"] = recorded.get("id_changed", False)
            entries.append(entry)

        known = load_all(path) if path else load_all()
        return {
            "version": REGISTRY_VERSION,
            "registry_path": path or registry_path(),
            "count": len(entries),
            "instances": entries,
            "registered_total": len(known.get("instances", {})),
        }
    finally:
        if studio_client is None:
            await client.close()
