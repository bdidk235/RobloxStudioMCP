"""Update-like wrapper around `multi_edit` for game-tree scripts.

Mirrors Claude Code Edit semantics for game-tree scripts:

* Reads first: the current source is always fetched via ``script_read``
  before editing (like Edit requires Read before Edit).
* Anchored, not exact-by-requirement: ``old_string`` is matched three ways in
  order — exact, then with ``script_read``'s ``NN→`` line prefixes removed,
  then line-by-line ignoring trailing whitespace. Every applied edit reports
  which one matched in ``matched_by`` so a fallback is never silent.
  ``strict=True`` refuses a fallback; it is off by default.
  A miss fails with an ``old_string not found`` error in strict mode (or is
  skipped with a warning by default).
* Uniqueness: an ``old_string`` matching multiple regions fails with a
  ``Found multiple matches for old_string ... Provide more surrounding
  lines ... or set replace_all=True`` error in strict mode (or is skipped
  as ambiguous by default).  Set ``replace_all=True`` on an edit — like
  Edit's ``replaceAll`` — to replace every occurrence instead.
* No-op guard: ``old_string`` and ``new_string`` must be different.
* Sequential: edits apply in order, each operating on the result of the
  previous edit (like multi-edit).  Batches return a summary of what was
  applied, skipped, and warned.

This module provides :func:`update_script` which wraps the raw
``multi_edit`` tool with exactly that behaviour.

Usage
-----
::

    import asyncio
    from roblox_studio_mcp.extended import RobloxStudio, update_script

    async def main():
        async with await RobloxStudio.connect() as studio:
            result = await update_script(
                studio,
                "game.ServerScriptService.MyScript",
                edits=[
                    ("old text", "new text"),
                    ("missing text", "replaced"),       # skipped (not found)
                    ("same text", "same text"),          # skipped (no-op)
                    ({"old_string": "x", "new_string": "y", "replace_all": True}),
                ],
            )
            print(result)

    asyncio.run(main())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple, Union

from .writer import _strip_line_prefixes, validate_game_tree_path, write_script
from .errors import INVALID_ARGUMENT, ToolError, describe
from ..roblox import RobloxStudio

# A single edit: an (old_string, new_string) tuple, or a dict with
# old_string/new_string (snake_case wire format) or oldString/newString
# (Claude Code Edit format), plus an optional replace_all/replaceAll flag
# mirroring Edit's replaceAll.
EditInput = Union[Tuple[str, str], Mapping[str, Any]]


def _edit_fault(index: int, field: str, problem: str, remedy: str) -> ToolError:
    """The failure raised for one edit in a batch.

    Built in one place: naming the field and the element index here is what
    stops ``edits[3].old_string`` from decaying back into ``Edit 3``.

    Every one of these is the caller's own edit, so every one is
    ``INVALID_ARGUMENT``.

    Notably *not* ``NOT_FOUND``, which is what the old "old_string not found in
    content" text classified to, by accident of the substring "not found". The
    Instance and the script were both found and read successfully; it was the
    caller's ``old_string`` that did not appear in the text. ``NOT_FOUND`` means
    "the thing you named is not in the DataModel", and an agent branching on it
    would go re-listing the DataModel for a script it had just read. The fix is
    to copy the exact text back, which is a different action entirely.
    """
    return ToolError(
        INVALID_ARGUMENT,
        ("edits[%d].%s %s %s" if field else "edits[%d] %s %s")
        % ((index, field, problem, remedy) if field else (index, problem, remedy)),
    )


@dataclass
class UpdateResult:
    """Summary of an ``update_script`` call."""
    updated: List[int] = field(default_factory=list)
    skipped_no_match: List[int] = field(default_factory=list)
    skipped_no_op: List[int] = field(default_factory=list)
    skipped_ambiguous: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: Per applied edit, which anchor pass matched: ``exact``,
    #: ``prefix-stripped`` or ``rstrip``. Every edit is listed, so the odd ones
    #: out are visible without reading the warnings.
    matched_by: Dict[int, str] = field(default_factory=dict)

    def __str__(self) -> str:
        parts = []
        if self.updated:
            parts.append(f"Updated edits at positions: {self.updated}")
        loose = {i: s for i, s in self.matched_by.items() if s != "exact"}
        if loose:
            parts.append(f"Matched without an exact hit: {loose}")
        if self.skipped_no_match:
            parts.append(f"Skipped (no match): positions {self.skipped_no_match}")
        if self.skipped_no_op:
            parts.append(f"Skipped (no-op): positions {self.skipped_no_op}")
        if self.skipped_ambiguous:
            parts.append(f"Skipped (ambiguous, multiple matches): positions {self.skipped_ambiguous}")
        return "; ".join(parts) if parts else "No changes."


def _normalize_edit(edit: EditInput, index: int) -> Tuple[str, str, bool]:
    """Normalize one edit to an (old_string, new_string, replace_all) triple.

    ``index`` is threaded in so the error names which element of the batch is
    wrong. ``update_script`` is also a public library entry point, so these
    faults are reachable without going through the server's own validation.
    """
    if isinstance(edit, (tuple, list)):
        old_str, new_str = edit
        return old_str, new_str, False
    if isinstance(edit, Mapping):
        old_str = edit.get("old_string", edit.get("oldString"))
        new_str = edit.get("new_string", edit.get("newString"))
        replace_all = bool(edit.get("replace_all", edit.get("replaceAll", False)))
        if not isinstance(old_str, str):
            raise _edit_fault(
                index, "old_string", "must be a string,",
                f"got {describe(old_str)}.",
            )
        if not isinstance(new_str, str):
            raise _edit_fault(
                index, "new_string", "must be a string,",
                f"got {describe(new_str)}. Use \"\" to delete the matched text.",
            )
        return old_str, new_str, replace_all
    raise _edit_fault(
        index, "", "must be an (old_string, new_string) tuple or dict,",
        f"got {describe(edit)}.",
    )


def _all_spans(source: str, needle: str) -> List[Tuple[int, int]]:
    """Every ``(start, end)`` where ``needle`` appears, overlaps included."""
    spans: List[Tuple[int, int]] = []
    start = source.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = source.find(needle, start + 1)
    return spans


def _span_for_line_run(source: str, query_lines: List[str]) -> List[Tuple[int, int]]:
    """Spans where consecutive lines match ``query_lines`` ignoring trailing space.

    Both sides are rstripped. Only one would make the fallback one-directional:
    a query pasted with trailing spaces would never match a clean file.
    """
    src_lines = source.split("\n")
    wanted = [line.rstrip() for line in query_lines]
    count = len(wanted)
    if count == 0 or count > len(src_lines):
        return []
    offsets = [0]
    for line in src_lines[:-1]:
        offsets.append(offsets[-1] + len(line) + 1)
    spans: List[Tuple[int, int]] = []
    for start in range(len(src_lines) - count + 1):
        if all(src_lines[start + k].rstrip() == wanted[k] for k in range(count)):
            last = start + count - 1
            spans.append((offsets[start], offsets[last] + len(src_lines[last])))
    return spans


def _locate(source: str, old_str: str) -> Tuple[List[Tuple[int, int]], str]:
    """Every span ``old_str`` anchors to, plus the strategy that found them.

    Three passes, stopping at the first that finds anything:

    ``exact``           - the bytes match, as they always did.
    ``prefix-stripped`` - the caller pasted ``script_read`` output. Those
                          ``NN→`` prefixes are already stripped from the file
                          side, so refusing them was this tool rejecting the
                          text it had just handed over.
    ``rstrip``          - lines match once trailing whitespace is ignored.
                          Internal whitespace and indentation are deliberately
                          **not** ignored: reflowing those turns a visible miss
                          into a plausible wrong edit, which is the one outcome
                          a caller cannot detect afterwards.
    """
    spans = _all_spans(source, old_str)
    if spans:
        return spans, "exact"

    stripped = _strip_line_prefixes(old_str)
    if stripped != old_str:
        spans = _all_spans(source, stripped)
        if spans:
            return spans, "prefix-stripped"

    return _span_for_line_run(source, old_str.split("\n")), "rstrip"


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


async def _apply_batch(
    studio: RobloxStudio,
    target_path: str,
    current_source: str,
    normalized: List[Tuple[str, str, bool]],
    *,
    skip_missing: bool,
    skip_no_ops: bool,
    strict: bool,
) -> UpdateResult:
    """Apply the batch in memory, then write the body once.

    One path for every batch. It used to be two - ``multi_edit`` for plain
    edits and this loop when any edit set ``replace_all`` - which meant two
    matching implementations and two places for an anchor rule to drift. The
    fallbacks below have to run here anyway, because ``multi_edit`` matches on
    the file's raw bytes and cannot express "equal after rstrip".
    """
    summary = UpdateResult()
    evolving = current_source

    for i, (old_str, new_str, replace_all) in enumerate(normalized):
        if old_str == new_str:
            summary.skipped_no_op.append(i)
            if skip_no_ops:
                summary.warnings.append(
                    f"Edit {i}: old_string matches new_string — skipped as no-op."
                )
                continue
            raise _edit_fault(
                i, "old_string", "equals new_string.",
                "Send different text, or set skip_no_ops=true to drop it.",
            )

        if not old_str:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string must not be empty — skipped."
                )
                continue
            raise _edit_fault(
                i, "old_string", "is empty.",
                "An empty old_string has no unique match; send the text to replace.",
            )

        spans, strategy = _locate(evolving, old_str)

        if not spans:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string not found, even after ignoring "
                    f"trailing whitespace — skipped."
                )
                continue
            raise _edit_fault(
                i, "old_string", "not found in content.",
                "Re-read the script and copy the text exactly. Matching tries "
                "an exact match, then the text with script_read's line prefixes "
                "removed, then line-by-line ignoring trailing whitespace.",
            )

        if len(spans) > 1 and not replace_all:
            summary.skipped_ambiguous.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: matched {len(spans)} times ({strategy}) — "
                    f"skipped as ambiguous. Add surrounding lines to narrow it "
                    f"to a unique block, or set replace_all=True."
                )
                continue
            raise _edit_fault(
                i, "old_string",
                f"matched {len(spans)} times ({strategy}); exactly 1 is required.",
                "Add surrounding lines to narrow it to a unique block, or set "
                "replace_all=true on this edit to replace all occurrences.",
            )

        if strategy != "exact":
            if strict:
                raise _edit_fault(
                    i, "old_string",
                    f"matched only after the {strategy} fallback, and strict is on.",
                    "Send the text exactly as it appears in the script, or set "
                    "strict=false to accept the fallback.",
                )
            summary.warnings.append(
                f"Edit {i}: matched on {strategy} at line {_line_of(evolving, spans[0][0])} "
                f"— the text is not byte-identical to the script."
            )

        summary.matched_by[i] = strategy
        for start, end in reversed(spans):
            evolving = evolving[:start] + new_str + evolving[end:]
        summary.updated.append(i)

    if not summary.updated:
        summary.warnings.append("No valid edits to apply.")
        return summary

    if evolving == current_source:
        return summary

    await write_script(studio, target_path, evolving)
    return summary


async def update_script(
    studio: RobloxStudio,
    target_path: str,
    edits: Sequence[EditInput],
    *,
    skip_missing: bool = True,
    skip_no_ops: bool = True,
    strict: bool = False,
) -> UpdateResult:
    """Apply edits to a game-tree script with graceful skipping.

    Reads first, applies in sequence in memory (each edit operating on the
    result of the previous one), and writes the body once.

    Anchoring has three passes — exact, prefix-stripped, trailing-whitespace —
    and every applied edit reports which one matched in ``matched_by``. A
    fallback is therefore never silent. ``strict=True`` refuses a fallback
    instead of applying it; it is off by default because the fallbacks exist to
    absorb the two shapes of text this package itself emits (``script_read``
    output and code copied out of an editor), and refusing those by default was
    the failure this replaced.
    """
    if not edits:
        return UpdateResult()

    validate_game_tree_path(target_path, field="target_path")

    # Read first (like Edit requires Read before Edit), and normalise the file
    # side the same way the query side is normalised in `_locate`.
    result = await studio.script_read(target_path)
    current_source = _strip_line_prefixes(result.text())

    normalized = [_normalize_edit(e, i) for i, e in enumerate(edits)]

    return await _apply_batch(
        studio,
        target_path,
        current_source,
        normalized,
        skip_missing=skip_missing,
        skip_no_ops=skip_no_ops,
        strict=strict,
    )
