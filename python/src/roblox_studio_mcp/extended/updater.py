"""Update-like wrapper around `multi_edit` for game-tree scripts.

Mirrors Claude Code Edit semantics for game-tree scripts:

* Reads first: the current source is always fetched via ``script_read``
  before editing (like Edit requires Read before Edit).
* Exact matching: ``old_string`` must match exactly.  A missing
  ``old_string`` fails with an ``old_string not found in content`` error in
  strict mode (or is skipped with a warning by default).
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, TypedDict, Union

from .writer import _strip_line_prefixes, _GAME_TREE_PREFIX, write_script
from .errors import INVALID_ARGUMENT, ToolError, describe
from ..roblox import RobloxStudio

# A single edit: an (old_string, new_string) tuple, or a dict with
# old_string/new_string (snake_case wire format) or oldString/newString
# (Claude Code Edit format), plus an optional replace_all/replaceAll flag
# mirroring Edit's replaceAll.
EditInput = Union[Tuple[str, str], Mapping[str, Any]]


class MultiEditEntry(TypedDict):
    """One element of the ``edits`` array sent to the raw ``multi_edit`` tool.

    This is the wire format, not our own: the keys are ``old_string`` and
    ``new_string`` because that is what ``multi_edit`` reads, and there is no
    ``replace_all`` because the raw tool requires exactly one match per
    ``old_string`` and so cannot express it. That constraint is why
    :func:`update_script` has a separate sequential path for any edit that sets
    the flag.

    Typed because it is the argument to an outbound tool call: a misspelled key
    here would be serialised straight into the request, where nothing on this
    side would complain.
    """

    old_string: str
    new_string: str


def _edit_fault(index: int, field: str, problem: str, remedy: str) -> ToolError:
    """The strict-mode failure for one edit in a batch.

    Built in one place because the same four faults were raised twice - once in
    the ``replace_all`` path and once in the batch path - and the two copies had
    already drifted. Naming the field and the element index here is what stops
    ``edits[3].old_string`` from decaying back into ``Edit 3``.

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

    def __str__(self) -> str:
        parts = []
        if self.updated:
            parts.append(f"Updated edits at positions: {self.updated}")
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


async def _apply_sequentially(
    studio: RobloxStudio,
    target_path: str,
    current_source: str,
    normalized: List[Tuple[str, str, bool]],
    *,
    skip_missing: bool,
    skip_no_ops: bool,
) -> UpdateResult:
    """Apply a batch sequentially in memory, then write the final body once.

    Used when any edit sets ``replace_all`` (like Edit's ``replaceAll``).
    Each edit operates on the result of the previous edit.  Edits without
    ``replace_all`` still require exactly one match at application time.
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

        occurrences = evolving.count(old_str)
        if occurrences == 0:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string not found in content — skipped."
                )
                continue
            raise _edit_fault(
                i, "old_string", "not found in content.",
                "Re-read the script and copy the text exactly - matching is "
                "exact, including indentation and line endings.",
            )

        if occurrences > 1 and not replace_all:
            summary.skipped_ambiguous.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: Found multiple matches for old_string "
                    f"({occurrences} occurrences) — skipped as ambiguous. "
                    f"Provide more surrounding lines to narrow it to a unique "
                    f"block, or set replace_all=True to replace all occurrences."
                )
                continue
            raise _edit_fault(
                i, "old_string",
                f"matched {occurrences} times; exactly 1 is required.",
                "Add surrounding lines to narrow it to a unique block, or set "
                "replace_all=true on this edit to replace all occurrences.",
            )

        evolving = evolving.replace(old_str, new_str) if replace_all else evolving.replace(old_str, new_str, 1)
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
) -> UpdateResult:
    """Apply edits to a game-tree script with graceful skipping.

    Like Claude Code Edit, the current source is read first, edits apply in
    sequence (each operating on the result of the previous edit), and every
    edit must be valid unless skipping is enabled.

    Parameters
    ----------
    studio
        An initialised :class:`RobloxStudio` instance.
    target_path
        DataModel dot-path, e.g. ``"game.ServerScriptService.MyScript"``.
    edits
        A sequence of ``(old_string, new_string)`` tuples or dicts with
        ``old_string``/``new_string`` (plus optional ``replace_all``, like
        Edit's ``replaceAll``, to replace every occurrence).
    skip_missing
        If true (default), skip edits whose ``old_string`` is missing or
        ambiguous with a warning.  If false, raise an Edit-like error.
    skip_no_ops
        If true (default), skip edits where ``old_string == new_string``.
        If false, raise an error for such edits.

    Returns
    -------
    UpdateResult
        Summary of what was applied and what was skipped.
    """
    if not target_path.startswith(_GAME_TREE_PREFIX):
        raise ToolError(
            INVALID_ARGUMENT,
            f"target_path must be a game-tree path starting with "
            f"{describe(_GAME_TREE_PREFIX)}, got {describe(target_path)}. "
            f"File-system paths go to write_script.",
        )

    # Read current source first (like Edit requires Read before Edit).
    result = await studio.script_read(target_path)
    current_source = _strip_line_prefixes(result.text())

    normalized = [_normalize_edit(e, i) for i, e in enumerate(edits)]

    # When any edit opts into replace-all, apply the batch sequentially in
    # memory and write the final body once.  The raw multi_edit requires
    # exactly one match per old_string, so it cannot express replace-all.
    if any(replace_all for _, _, replace_all in normalized):
        return await _apply_sequentially(
            studio, target_path, current_source, normalized,
            skip_missing=skip_missing, skip_no_ops=skip_no_ops,
        )

    # Classify each edit against the current source.
    valid_edits: List[MultiEditEntry] = []
    summary = UpdateResult()

    for i, (old_str, new_str, _replace_all) in enumerate(normalized):
        # No-op guard: oldString and newString must be different.
        if old_str == new_str:
            summary.skipped_no_op.append(i)
            if skip_no_ops:
                summary.warnings.append(
                    f"Edit {i}: old_string matches new_string — skipped as no-op."
                )
                continue
            else:
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
            else:
                raise _edit_fault(
                    i, "old_string", "is empty.",
                    "An empty old_string has no unique match; "
                    "send the text to replace.",
                )

        occurrences = current_source.count(old_str)
        # Check if old_string exists at all.
        if occurrences == 0:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string not found in content — skipped."
                )
                continue
            else:
                raise _edit_fault(
                    i, "old_string", "not found in content.",
                    "Re-read the script and copy the text exactly - matching is "
                    "exact, including indentation and line endings.",
                )

        # multi_edit requires exactly one match; duplicates would fail downstream.
        if occurrences > 1:
            summary.skipped_ambiguous.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: Found multiple matches for old_string "
                    f"({occurrences} occurrences) — skipped as ambiguous. "
                    f"Provide more surrounding lines to narrow it to a unique "
                    f"block, or set replace_all=True to replace all occurrences."
                )
                continue
            else:
                raise _edit_fault(
                    i, "old_string",
                    f"matched {occurrences} times; exactly 1 is required.",
                    "Add surrounding lines to narrow it to a unique block, or "
                    "set replace_all=true on this edit to replace all occurrences.",
                )

        valid_edits.append({"old_string": old_str, "new_string": new_str})
        summary.updated.append(i)

    if not valid_edits:
        summary.warnings.append("No valid edits to apply.")
        return summary

    # Apply remaining edits in a single atomic multi_edit call.
    await studio.call(
        "multi_edit",
        {
            "file_path": target_path,
            "datamodel_type": "Edit",
            "edits": valid_edits,
        },
    )

    return summary
