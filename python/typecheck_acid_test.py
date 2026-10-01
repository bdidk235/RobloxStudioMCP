"""Acid test: which type checker would have caught the bug that shipped today?

The real defect was in `_call_watch_output`:

    result = await watch_output(...)          # -> {"new_lines", "total_lines", "last_line"}
    lines = [... for ln in str(result.get("text", "")).split("\\n") ...]

`result` has no `text` key, so `.get("text", "")` returned `""` on every call and
the tool reported `{"returned": 0}` with `isError: false`. It shipped because the
only test on the tool asserted its *description*.

**The honest complication, stated up front:** written exactly as above, with
`.get()` and a default, *neither* checker can catch it. `dict.get` accepts any
key and any default, and `watch_output` was annotated `-> Dict[str, Any]`, so
every key access is legal. That annotation is the real hole.

So this file tests the thing that actually matters: **does the checker catch the
bug once the return type is honest?** If neither checker helps until the types are
fixed up front, then the type checker is not the fix - the annotation is, and the
tool should be chosen on the other evidence.

Each block is run through both checkers. The verdict is printed by the runner, not
asserted here, because "which checker is better" is a question for the comparison
script to answer from measured output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


# --- the shape as it is actually returned ---------------------------------- #


class WatchResult(TypedDict):
    """The honest return type. This alone is most of the fix."""

    new_lines: List[str]
    total_lines: int
    last_line: Optional[str]


def watch_output_typed() -> WatchResult:
    return {"new_lines": ["a"], "total_lines": 1, "last_line": "a"}


def watch_output_untyped() -> Dict[str, Any]:
    """How it is annotated today. `Dict[str, Any]` disables all key checking."""
    return {"new_lines": ["a"], "total_lines": 1, "last_line": "a"}


# --- the shipped bug, against a typed result ------------------------------- #
# Both of these SHOULD be flagged. They are the exact defect.


def bug_typed_get(result: WatchResult) -> List[str]:
    # `.get` on a TypedDict with a missing key: mypy flags this, pyright may not.
    return [ln for ln in str(result.get("text", "")).split("\\n") if ln.strip()]


def bug_typed_subscript(result: WatchResult) -> List[str]:
    # The same bug without the forgiving `.get`. Both must flag it.
    return [ln for ln in str(result["text"]).split("\\n") if ln.strip()]


def bug_untyped(result: Dict[str, Any]) -> List[str]:
    # The bug against the annotation actually in use today. Neither can see it.
    return [ln for ln in str(result.get("text", "")).split("\\n") if ln.strip()]


# --- the None-deref class, which both did find ----------------------------- #


def maybe_identity(identity: Optional[Dict[str, Any]]) -> Any:
    """The shape behind instance.py:823, which both checkers flagged for real."""
    return identity.get("task")


def maybe_identity_ok(identity: Optional[Dict[str, Any]]) -> Any:
    return identity["task"] if identity else None


# --- narrowing that mypy gets wrong ---------------------------------------- #


def narrowing_false_positive() -> None:
    """instance.py:727-728. mypy flags the call; the value is already narrowed
    and the code is correct. Recorded so the false positive is not mistaken for a
    defect, and so the checker's miss rate stays measurable."""
    rows: List[Dict[str, Any]] = []
    started: Dict[Any, Optional[float]] = {r["pid"]: None for r in rows}
    filtered = {pid: when for pid, when in started.items() if when is not None}
    reveal_type(filtered)  # pyright/mypy disagree about whether this is narrowed
    takes_optional_none(filtered)


def takes_optional_none(value: Optional[Dict[int, float]]) -> None:
    """Accepts None, which is why the call at instance.py:728 is not a defect."""
    return None
