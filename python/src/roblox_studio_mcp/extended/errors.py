"""Stable error codes, so a caller can branch instead of parsing prose.

Why this exists
---------------
Before this, every failure came back as ``{"code": -32000, "message": "<prose>"}``.
That is fine for a human reading a sentence and useless for code. A caller
cannot ask "was this the ambiguous-Studio case or the stale-id case?" without
matching on English, which breaks the moment Studio rewords a message. It also
means the *useful* part of an ambiguity error, the candidate list, is lost
inside the string.

So failures now carry a stable ``code`` plus structured ``data``:

    {"code": "AMBIGUOUS_STUDIO",
     "message": "3 Studios are connected; pass studio_id",
     "data": {"candidates": [{"studio_id": "...", "name": "..."}]}}

The codes are the contract. Messages are also load-bearing: the reader is an
agent deciding what to send next, so a message has to name the offending value,
state the accepted set where there is one, and say what to do instead. Wording
may still change, but not to the point of withholding the accepted set.

That is also why a caller fault raises :class:`ToolError` at the raise site
rather than a bare ``ValueError`` left for ``classify()`` to sort out by
substring. A code derived from prose is a code that moves when the prose does,
and an agent that branches on it fails silently.

The engine is a poor guide to its own failures, which is why classification is
substring matching over messages that were actually observed, and why an
unrecognised error falls through to ``UNKNOWN`` rather than being forced into a
bucket. Two measured examples of the engine misleading us:

* ``AddPlayers`` said "can only be called from the server DataModel of a running
  Studio test session" while the Server DataModel *was* available and a session
  *was* running. The real cause was that the session could not host players.
* A connection refused as "Edit datamodel is not available in Play mode" is
  genuinely what it says, and the fix is to target a different DataModel.

So a code describes *what we concluded*, and the message is kept verbatim
alongside it. Nothing is hidden, and nothing is asserted more strongly than we
know.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

# --- codes ------------------------------------------------------------------ #
NO_STUDIO = "NO_STUDIO"
STALE_STUDIO_ID = "STALE_STUDIO_ID"
AMBIGUOUS_STUDIO = "AMBIGUOUS_STUDIO"
DATAMODAL_UNAVAILABLE = "DATAMODAL_UNAVAILABLE"
PLACE_NOT_OPEN = "PLACE_NOT_OPEN"
NOT_FOUND = "NOT_FOUND"
SIZE_LIMIT = "SIZE_LIMIT"
TEST_BUSY = "TEST_BUSY"
TEST_REFUSED = "TEST_REFUSED"
LAUNCH_FAILED = "LAUNCH_FAILED"
TIMEOUT = "TIMEOUT"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
LUA_ERROR = "LUA_ERROR"
CAPABILITY_DENIED = "CAPABILITY_DENIED"
UNKNOWN = "UNKNOWN"

#: Every code, for tests and for validating anything a caller sends back.
ALL_CODES = frozenset({
    NO_STUDIO, STALE_STUDIO_ID, AMBIGUOUS_STUDIO, DATAMODAL_UNAVAILABLE,
    PLACE_NOT_OPEN, NOT_FOUND, SIZE_LIMIT, TEST_BUSY, TEST_REFUSED,
    LAUNCH_FAILED, TIMEOUT, INVALID_ARGUMENT, LUA_ERROR, CAPABILITY_DENIED,
    UNKNOWN,
})

#: Accepted spellings that mean another code.
#:
#: ``PLACE_NOT_OPEN`` is the semantically nicer name for the situation, and it
#: was published, so removing it would break a caller that already branches on
#: it. But its own wording -- ``"place is not open"`` -- is claimed by
#: ``STALE_STUDIO_ID`` on purpose: a stale ``studio_id`` genuinely *presents* as
#: that message, and the recovery (re-pin from a current id) is the correct
#: recovery for both. So ``STALE_STUDIO_ID`` owns the wording and emits the code,
#: and this map is what stops a branch on ``PLACE_NOT_OPEN`` being dead code.
#:
#: Use :func:`canonical_code` on anything a caller is about to compare:
#:
#:     if canonical_code(error.code) in (STALE_STUDIO_ID, PLACE_NOT_OPEN):
#:
#: Recorded 2026-10-01. The alias was *documented as shipped* in ``TODO.md``
#: before it existed in the tree, which is exactly the failure that file keeps
#: cataloguing: a decision written down is not an implementation.
CODE_ALIASES: Dict[str, str] = {
    PLACE_NOT_OPEN: STALE_STUDIO_ID,
}


def canonical_code(code: str) -> str:
    """Resolve an accepted alias to the code this implementation emits.

    Compares equal either way. Unaliased codes pass through unchanged, and an
    unrecognised code returns as-is rather than raising, so this is safe to call
    on a code from the wire before deciding whether to trust it.
    """
    return CODE_ALIASES.get(code, code)

#: Ordered: the first match wins, so put the specific patterns above the general
#: ones. "not connected" would otherwise swallow the ambiguity error.
_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (NO_STUDIO, ("no roblox studio instances are connected",)),
    (STALE_STUDIO_ID, (
        "requested studio_id is not connected",
        "place is not open",
        "not connected",
    )),
    (AMBIGUOUS_STUDIO, (
        "more than one studio",
        "multiple studio",
        "ambiguous",
    )),
    (TEST_BUSY, (
        "a previous one is still in progress",
    )),
    (TEST_REFUSED, (
        "addplayers",
        "endtest",
        "leavetest",
    )),
    (DATAMODAL_UNAVAILABLE, (
        "datamodel is not available",
        "is not a valid service name",
    )),
    (CAPABILITY_DENIED, (
        "requires the robloxscript",
        "requires robloxscript",
        "not permitted",
        "plugin security",
        "requires elevated",
    )),
    (SIZE_LIMIT, (
        "bad allocation",
        "out of memory",
        "string too long",
    )),
    (LAUNCH_FAILED, (
        # Without these, `LAUNCH_FAILED` was in the public vocabulary but
        # **unreachable**: no pattern produced it and no handler raised it, so
        # every launch failure classified as UNKNOWN and a caller branching on
        # it silently never matched. That is worse than a missing code - it
        # looks handled.
        #
        # Found by an exhaustiveness check over the closed set of codes, not by
        # pyright and not by any test: `ToolError("LAUNCH_FAILED", ...)` type
        # checks in one place. Only "is this set fully connected?" finds it.
        "no complete robloxstudio",
        "is not installed",
        "did not open a place",
        "never opened",
        "mcp is not enabled",
        "could not launch",
        "failed to launch",
        "no robloxstudio executable",
    )),
    (TIMEOUT, (
        "timed out",
        "timeout",
    )),
    (NOT_FOUND, (
        "could not find any instances",
        "script not found",
        "not found",
    )),
)

#: Luau faults, matching the caller's own code rather than an API refusal. These
#: are the caller's bug, and conflating them with the engine saying "no" is what
#: made a failed probe look like a transport fault. Checked before the message
#: table so a syntax error is never claimed by a looser pattern.
#:
#: **This is the only place ``LUA_ERROR`` is produced from message text.** It
#: used to also appear in :data:`_PATTERNS` as twelve literal needles, which
#: could never fire: `classify` runs this regex first, and all twelve needles
#: are substrings of it (measured, all 12). So the table copy was a second
#: spelling of one rule - the shape that drifts - and its presence in
#: ``_PATTERNS`` read as coverage the table was not actually providing.
_LUA_FAULT = re.compile(
    r"(attempt to (call|index|concatenate|perform arithmetic)|"
    r"is not a valid member|"
    r"failed to parse|"
    r"unexpected symbol|"
    r"invalid value \(|"
    r"unbalanced|"
    r"malformed|"
    # Observed verbatim from Studio: a type mismatch on a service argument,
    # reported as a cast failure rather than an API refusal.
    r"unable to cast|"
    r"cannot cast)",
    re.IGNORECASE,
)


class ToolError(Exception):
    """A failure with a stable code and structured data.

    Raise this instead of ``ValueError`` so the code survives to the caller.
    """

    def __init__(self, code: str, message: str, **data: Any) -> None:
        if code not in ALL_CODES:
            # Report the message as well: a bare "unknown error code" leaves the
            # caller knowing only that something broke. The `INTERNAL_ERROR`
            # capture case lost "the capture succeeded, the write failed" here.
            raise AssertionError(
                f"unknown error code {code!r}; the message was: {message}"
            )
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: Dict[str, Any] = data

    def to_error(self) -> Dict[str, Any]:
        """The JSON-RPC ``error`` object."""
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data:
            payload["data"] = self.data
        return payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ToolError({self.code!r}, {self.message!r}, {self.data!r})"



def describe(value: Any) -> str:
    """Render a received argument value for an error message.

    A message that says "must be a string" without the value cannot tell the
    caller whether it sent an empty string, 42 or null, and those three need
    three different fixes.

    The rendering is **JSON, not Python repr**, and that is the whole point:
    ``None``/``True``/``'x'`` read as ``null``/``true``/``"x"`` on the wire, so a
    caller that string-matches - which is exactly what an agent does - matches
    what it was actually sent. ``contract/errors.json`` pins the result.
    """
    try:
        # No space after a comma or a colon. A space there is invisible to a
        # human reading the error and fatal to a caller comparing byte for byte.
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def classify(exc: BaseException) -> ToolError:
    """Turn any exception into a :class:`ToolError`.

    Luau faults are checked **first**, before the message table. The ordering
    matters: "Failed to parse command code" contains "parse", and a later
    pattern could otherwise claim it, but a syntax error in the caller's own code
    is a different class of problem from the engine refusing a request, and the
    caller needs to be able to tell those apart.
    """
    if isinstance(exc, ToolError):
        return exc

    text = str(exc)

    if _LUA_FAULT.search(text):
        return ToolError(LUA_ERROR, text, classified_from="luau-fault-pattern")

    lowered = text.lower()
    for code, needles in _PATTERNS:
        if any(n in lowered for n in needles):
            return _with_recovery(ToolError(code, text, classified_from="message"))

    return ToolError(UNKNOWN, text, classified_from="default")


#: What to do next, keyed by code.
#:
#: **Added, not paraphrased.** The rule recorded above this table - keep the
#: engine's message verbatim - exists so a caller's own reading of the text is
#: never second-guessed. That is preserved: the engine's words come through
#: untouched and unchanged, and the recovery is appended after a blank line so
#: a substring match on the original still works.
#:
#: Only codes where the *fix is deterministic* get an entry. Several codes are
#: genuine dead ends with nothing useful to add, and a recovery line that says
#: "check that it is enabled" teaches a caller to keep trying things.
#:
#: Seeded with one code, from a specific complaint. Widening this is a decision
#: per code, not a formatting pass.
_RECOVERY: Dict[str, str] = {
    DATAMODAL_UNAVAILABLE: (
        "A datamodel operation ran where that datamodel does not exist: "
        "'Edit' needs Edit mode with no play session running, 'Server' and "
        "'Client' need a running play session. Check the Studio's actual mode "
        "first (get_studio_state, or the rsx-playtest skill) rather than "
        "assuming either direction. Note: "
        "extended_manage_instance(action='stop') terminates the Studio "
        "process; it does not end a play session."
    ),
}


def _with_recovery(error: ToolError) -> ToolError:
    """Return ``error`` with recovery text appended, if its code has any.

    Builds a new error rather than mutating. ``ToolError.__init__`` passes the
    message to ``Exception.__init__``, so ``str(error)`` reads ``args[0]`` while
    ``to_error()`` reads ``self.message`` - mutating one and not the other leaves
    the two permanently disagreeing, which is the exact class of disagreement
    this project keeps refusing to ship.
    """
    recovery = _RECOVERY.get(error.code)
    if recovery is None:
        return error
    data = dict(error.data)
    data["recovery"] = recovery
    # Blanked-line separated so the engine's sentence is still the first
    # paragraph and any `in message` check a caller wrote keeps passing.
    return ToolError(error.code, "%s\n\n%s" % (error.message, recovery), **data)


def is_lua_fault(text: str) -> bool:
    """True when a message is a Luau runtime fault rather than an API refusal."""
    return bool(_LUA_FAULT.search(text))
