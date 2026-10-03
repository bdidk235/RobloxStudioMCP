"""Waiting for a condition, entirely host-side.

Why host-side and not in Luau
------------------------------
Three ways to wait were tried and all three fail, for different reasons:

1. **Sleeping in the agent's code runtime.** It has no timer, so
   ``await new Promise(...)`` hangs until timeout. Not a Luau problem at all.
2. **``task.spawn`` inside a tool call.** The spawned thread dies with the call's
   context, so it never runs to completion. Worse, if it dies mid-test-call
   Studio's test service wedges permanently.
3. **A single blocking tool call.** Works, but is capped by the client timeout
   (120 s by default, and only the *first* leg of it is ours to choose).

The host process has a real event loop with real timers, so the wait belongs
here: a loop of small ``execute_luau`` calls, none of which needs to persist
between calls. That is why this needs no persistent Luau thread, and it is the
reason the whole thing works where the other three did not.

Design notes that matter in practice:

* **Adaptive polling.** A fixed interval is wrong at both ends: too slow to catch
  something that happens in 100 ms, and wasteful over a long wait. This starts
  fast and backs off, so the common case is quick and the long case is cheap.
* **A flat cap below the client timeout.** Returning ``timed_out`` with the last
  observed value beats being cut off with nothing.
* **The condition must be a boolean, and a truthy non-boolean is refused.**
  In Lua ``0`` is truthy, so ``return #Players:GetPlayers()`` would be satisfied
  immediately at one player. This used to come back ``satisfied: true`` with a
  ``warning`` alongside - **which asserts the very thing the caller asked
  about**, and a warning next to ``satisfied: true`` is a note nobody reads.
  It now raises ``INVALID_ARGUMENT`` naming the value it got.

  The split is deliberate and is not "non-boolean means error":

  * **truthy non-boolean** (``0``, ``""``, a count, a table) -> **error**. This
    is the case that manufactures a false success, which is the one thing this
    tool must never do.
  * ``nil`` -> **"not yet", keep polling**. ``return workspace.Foo`` and
    ``return obj and obj.done`` are ordinary, correct conditions that yield a
    non-boolean. Hard-erroring them would break the tool's main use, so ``nil``
    is treated as falsy exactly as Lua does.

  ``false`` is of course "not yet" too.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, List, Literal, Optional, TypedDict
from ..roblox import RobloxStudio
from ..types import CallToolResult
from .errors import ToolError, classify

#: First poll delay, seconds. Small, because most conditions resolve fast.
MIN_POLL = 0.25

#: Ceiling on the poll interval, so a long wait stops hammering Studio.
MAX_POLL = 2.0

#: Growth per poll once the condition looks settled.
BACKOFF = 1.5

#: Hard ceiling on the wait. Chosen so the wait budget *plus* one in-flight poll
#: still fits inside the 120 s client timeout: the budget is a target, and the
#: final poll can start just before it expires and run long. 90 + 30 = 120, so a
#: caller always gets a verdict rather than an opaque client-side timeout.
MAX_WAIT = 90.0

#: Per-poll ceiling, seconds. A healthy poll returns in ~1 s. A command Studio
#: cannot parse has wedged command execution before - measured 2026-09-30, twice:
#: the await never resolved and, requests being sequential, the wedged call
#: blocked every later one until the MCP was reconnected. Bounding the await
#: keeps the loop alive. Cancelling here does NOT cancel Studio-side execution -
#: it only frees this end - so consecutive hangs mean the Studio side is wedged,
#: not slow, and retrying is burning budget on polls that cannot return.
POLL_TIMEOUT = 30.0

#: Consecutive hung polls before giving up. One hang could be a slow Studio;
#: three in a row is a wedged one. The abort names the restart that fixes it,
#: because the alternative - burning the whole budget one hung poll at a time -
#: reports a timeout that reads as "condition never true" for a condition that
#: never ran.
MAX_HUNG_POLLS = 3

#: Number of identical consecutive results after which the wait is treated as
#: settled and the interval is widened aggressively.
SETTLED_AFTER = 3

#: Monotonic clock, injectable so tests need not wait in real time.
Clock = Callable[[], float]

#: Sleep in seconds, injectable for the same reason.
Sleeper = Callable[[float], Awaitable[None]]


class VirtualClock:
    """A clock that only moves when told to.

    Pairs with ``wait_for(sleep=clock.sleep)``: the wait's own idea of elapsed
    time advances by exactly the interval it asked to sleep, so a test proves
    the deadline logic without spending the deadline.

    ``asyncio.sleep(0)`` is yielded between steps so a test with many polls
    still reaches a suspension point and cannot starve the loop.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.slept: List[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


#: All three DataModels are supported, and ``loadstring`` was the only thing
#: stopping that. It does not resolve in play mode (user-confirmed, from a live
#: failure: ``loadstring() is not available`` in ``AssistantCommand``), so a
#: caller waiting on a condition in play mode got a fault that had nothing to do
#: with their condition. Removing it makes the ``pcall`` form work in every mode;
#: play mode was collateral damage from the wrong fix, not a limitation of
#: waiting itself.
#:
#: The residual hazard is narrower and is a Studio bug rather than ours - see
#: :data:`PROBE_CAVEAT`. A hung poll no longer wedges this server (each poll's
#: await is bounded; see :data:`POLL_TIMEOUT`), but nothing here can un-wedge
#: Studio itself, so consecutive hangs abort with the restart that fixes them.
PROBE_CAVEAT = (
    "a condition that does not parse is compiled as part of the command sent to "
    "Studio, and such a command has wedged Studio's command execution before; the "
    "parse happens before any handler runs, so a hung poll is bounded client-side "
    "but only a Studio restart clears the wedge. Prefer simple comparisons."
)


def build_probe(condition: str) -> str:
    """Wrap a caller condition so one call returns everything we need.

    **No ``loadstring``.** It was introduced to isolate a caller condition that
    failed to parse, and it did that job in Edit mode. But it does not resolve in
    play mode, so the tool was advertising a ``datamodel_type`` it could not
    honour - a technique that only works in one of two supported contexts is a
    trap, not a technique. Play mode works without it; the ``pcall`` form never
    needed it for anything except parse isolation.

    The condition is spliced in as **code**, not as a string literal. That
    distinction is the whole ballgame and it was got wrong once already: an
    earlier version of this fix kept ``_luau_string()`` and emitted
    ``return ([[#game:GetDescendants() > 50]])``, which returns the *string*
    ``"#game:GetDescendants() > 50"``. That string is truthy, so every wait
    reported ``satisfied: true`` on the first poll **without evaluating the
    condition at all** - a silent false success, the exact class this project
    exists to prevent. Caught only because the verdict's non-boolean warning fired.

    So: raw splice. A condition that parses is evaluated, and one that *faults* is
    caught and reported as ``THREW`` rather than crashing the poll. A condition
    that does not *parse* is not catchable here at all; see
    :data:`PROBE_CAVEAT`.

    The ``return (...)`` stays inside the function. Compiling the caller's text as
    a bare statement rejected ``true``, which is not a Luau statement, so the
    first real condition ever tried failed with "Expected identifier when parsing
    expression".
    """
    return (
        "local ok, value = pcall(function()\n"
        "  return (" + condition + ")\n"
        "end)\n"
        "if not ok then return \"false\\tTHREW\\t\" .. tostring(value) end\n"
        "return tostring(value)\n"
    )


def _luau_string(value: str) -> str:
    """A Luau long-bracket string literal, safe for any input.

    A long string is ``[`` + N ``=`` + ``[`` ... ``]`` + N ``=`` + ``]``. Two
    things took a wrong implementation to find:

    * **Only ``=`` is a level indicator.** ``]`` is not, so ``[]]`` is not a
      valid opener. Emitting it for a value containing no bracket produced code
      ``execute_luau`` rejected with "Failed to parse command code", so a
      perfectly good condition looked like a broken tool.
    * A level-0 string is closed by the first ``]]``, so ``]]`` anywhere in the
      value truncates it. Any other lone ``]`` is harmless, and ``=`` is
      harmless at any level.

    So one rule covers everything: **use level 1 exactly when the value contains
    ``]]``**, where the closer is ``]=]`` and a bare ``]]`` cannot match it.
    """
    pad = "=" if "]]" in value else ""
    return f"[{pad}[{value}]{pad}]"


#: The two ways a probe can fail, as emitted by :func:`build_probe`. Kept as a
#: ``Literal`` pair so a caller cannot grow a third spelling by accident:
#: ``wait_for`` branches on exactly these two, and a typo there would silently
#: fall through to the "timed out" verdict.
ProbeFault = Literal["ERR", "THREW"]


class ProbeResult(TypedDict):
    """What :func:`parse_probe` reads out of one probe reply.

    ``ok`` is the whole answer: a probe that faulted has ``ok`` false, and the
    distinction between "the condition is not true yet" and "the condition never
    ran" is carried by ``fault``, not by ``ok``. :func:`wait_for` depends on
    that split — reporting a compile error as a timeout is what turns a typo
    into a 90-second wait and no answer — so the four keys are pinned here
    rather than left to a ``Dict[str, Any]`` that every key access would accept.
    """

    ok: bool
    value: Optional[str]
    fault: Optional[ProbeFault]
    detail: str


class WaitVerdict(TypedDict):
    """The verdict :func:`wait_for` returns instead of raising on timeout.

    Every key is always present, including the empty ones: ``last_value`` is
    ``None`` rather than absent, so a caller never has to distinguish "no value
    observed" from "key missing".

    **``warning`` was removed**, and its absence is the fix rather than an
    oversight. It existed for one producer - a truthy non-boolean, reported as
    ``satisfied: true`` with a caveat beside it. That case now raises
    ``INVALID_ARGUMENT``, so nothing could produce the field and it would have
    shipped as a permanent ``null``. A field that is always null invites a
    caller to check it, find nothing, and conclude all is well.
    """

    satisfied: bool
    timed_out: bool
    last_value: Optional[str]
    polls: int
    timeout_seconds: float
    final_poll_seconds: float
    settled_repeats: int
    poll_errors: List[str]


def parse_probe(raw: str) -> ProbeResult:
    """Read the probe reply into ok / value / fault.

    ``fault`` is ``ERR`` when the condition would not compile, ``THREW`` when it
    compiled but faulted, and ``None`` when it simply evaluated. Both faults are
    raised by the caller, because a condition that never runs must not be
    reported as a timeout.
    """
    text = raw.strip()
    parts = text.split("\t")
    # A bare value means the condition returned something falsy-looking; that is
    # still a successful evaluation.
    if not parts or not parts[0]:
        return {"ok": False, "value": None, "fault": "THREW", "detail": text[:200] or "empty reply"}
    head = parts[0]
    rest = "\t".join(parts[1:])
    if head == "false" and len(parts) >= 2 and parts[1] in ("ERR", "THREW"):
        return {
            "ok": False,
            "value": None,
            "fault": parts[1],
            "detail": rest[:200],
        }
    return {"ok": True, "value": head, "fault": None, "detail": rest[:200]}


async def wait_for(
    studio: RobloxStudio,
    condition: str,
    timeout_seconds: float,
    *,
    datamodel_type: str = "Edit",
    poll_timeout: float = POLL_TIMEOUT,
    clock: Optional[Clock] = None,
    sleep: Optional[Sleeper] = None,
) -> WaitVerdict:
    """Poll ``condition`` until it is true, or the deadline passes.

    Returns a verdict rather than raising on timeout, because "not yet" is a
    normal answer here and the caller usually wants to see the last value.

    ``poll_timeout`` bounds each poll's await (test seam; the default is the
    module constant). A poll that exceeds it is a hung poll, not a slow one,
    and ``MAX_HUNG_POLLS`` consecutive hangs abort with ``TIMEOUT`` rather
    than burning the budget one dead poll at a time.

    ``clock`` and ``sleep`` are injectable because the alternative is a test
    suite that spends seconds of wall time proving a timeout works. The Node
    implementation already had this seam; see ``waiting.ts``.
    """
    if not condition or not condition.strip():
        raise ToolError("INVALID_ARGUMENT", "condition must be a non-empty expression")
    budget = max(MIN_POLL, min(float(timeout_seconds), MAX_WAIT))
    probe = build_probe(condition)
    now = clock or time.monotonic
    nap = sleep or asyncio.sleep
    deadline = now() + budget

    delay = MIN_POLL
    last: Optional[str] = None
    repeats = 0
    polls = 0
    hung = 0
    errors: List[str] = []

    while True:
        polls += 1
        try:
            result: CallToolResult = await asyncio.wait_for(
                studio.call(
                    "execute_luau",
                    {"code": probe, "datamodel_type": datamodel_type},
                ),
                timeout=poll_timeout,
            )
        except asyncio.TimeoutError:
            # The Studio side never replied. Cancelling frees this end only;
            # Studio-side execution is unaffected, so a second hang is evidence
            # the wedge is over there, not here. Do NOT reset the budget - a
            # condition that never runs must not consume a wait - but do not
            # loop forever either: consecutive hangs abort with the restart
            # that fixes them.
            hung += 1
            errors.append(
                "poll %d hung (no reply within %.0fs)" % (polls, poll_timeout)
            )
            if hung >= MAX_HUNG_POLLS:
                raise ToolError(
                    "TIMEOUT",
                    "condition never executed: %d consecutive polls hung with "
                    "no reply. Studio-side command execution is likely wedged "
                    "- an unparseable condition does this - and reconnecting "
                    "this server will not clear it. Restart the Studio, fix "
                    "the condition, and try again." % MAX_HUNG_POLLS,
                    condition=condition,
                )
            await nap(delay)
            delay = min(delay * BACKOFF, MAX_POLL)
            continue
        except Exception as exc:  # noqa: BLE001 - a poll failure is data, not a crash
            err = classify(exc)
            # A missing DataModel will not appear on its own, so surface it now
            # rather than after burning the whole budget on a doomed wait.
            if err.code == "DATAMODAL_UNAVAILABLE":
                raise err
            errors.append(err.message[:160])
            await nap(delay)
            delay = min(delay * BACKOFF, MAX_POLL)
            continue

        parsed = parse_probe(result.text())
        hung = 0  # any reply proves Studio executed something; only hangs count
        if not parsed["ok"]:
            # A condition that never runs must not be reported as a timeout:
            # that is what turns a typo into a 90-second wait and no answer.
            if parsed["fault"] == "ERR":
                raise ToolError(
                    "INVALID_ARGUMENT",
                    f"condition does not compile: {parsed['detail']}",
                    condition=condition,
                )
            raise ToolError(
                "LUA_ERROR",
                f"condition raised at runtime: {parsed['detail']}",
                condition=condition,
            )

        if parsed["value"] == last:
            repeats += 1
        else:
            repeats = 0
            last = parsed["value"]

        # A truthy non-boolean is the one case that can manufacture a false
        # success, so it is refused rather than reported with a caveat.
        # `nil` is NOT in this branch: it is falsy in Lua and
        # `return workspace.Foo` is a normal condition, not a mistake.
        if parsed["value"] not in ("true", "false", "nil"):
            raise ToolError(
                "INVALID_ARGUMENT",
                f"condition returned {parsed['value']!r}, not a boolean; "
                "in Lua 0 and '' are both truthy, so this would have been "
                "reported as satisfied. Return true or false - `return "
                "workspace.Foo` is fine, but compare it or wrap it in a "
                "`and .. or false`.",
                condition=condition,
                returned=parsed["value"],
            )
        satisfied = parsed["value"] == "true"
        if satisfied:
            return _verdict(True, last, polls, budget, delay, repeats, errors)

        remaining = deadline - now()
        if remaining <= 0:
            return _verdict(False, last, polls, budget, delay, repeats, errors)
        await nap(min(delay, remaining))
        if repeats >= SETTLED_AFTER:
            delay = min(delay * BACKOFF, MAX_POLL)
        else:
            delay = min(delay * 1.25, MAX_POLL)

    raise AssertionError("unreachable")


def _verdict(
    satisfied: bool,
    last: Optional[str],
    polls: int,
    budget: float,
    delay: float,
    repeats: int,
    errors: List[str],
) -> WaitVerdict:
    return {
        "satisfied": satisfied,
        "timed_out": not satisfied,
        "last_value": last,
        "polls": polls,
        "timeout_seconds": budget,
        "final_poll_seconds": round(delay, 3),
        "settled_repeats": repeats,
        "poll_errors": errors[:3],
    }


__all__ = [
    "MIN_POLL", "MAX_POLL", "BACKOFF", "MAX_WAIT", "SETTLED_AFTER",
    "POLL_TIMEOUT", "MAX_HUNG_POLLS", "PROBE_CAVEAT",
    "ProbeFault", "ProbeResult", "WaitVerdict",
    "build_probe", "parse_probe", "wait_for",
]
