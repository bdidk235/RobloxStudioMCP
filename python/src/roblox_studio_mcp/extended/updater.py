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

This module provides :func:`update_like_multi_edit` which wraps the raw
``multi_edit`` tool with exactly that behaviour.

Usage
-----
::

    import asyncio
    from roblox_studio_mcp.extended import RobloxStudio, update_like_multi_edit

    async def main():
        async with await RobloxStudio.connect() as studio:
            result = await update_like_multi_edit(
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .writer import _strip_line_prefixes, _GAME_TREE_PREFIX, write_like_multi_edit
from ..roblox import RobloxStudio

# A single edit: an (old_string, new_string) tuple, or a dict with
# old_string/new_string (snake_case wire format) or oldString/newString
# (Claude Code Edit format), plus an optional replace_all/replaceAll flag
# mirroring Edit's replaceAll.
EditInput = Union[Tuple[str, str], Mapping[str, Any]]


@dataclass
class UpdateResult:
    """Summary of an ``update_like_multi_edit`` call."""
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


def _normalize_edit(edit: EditInput) -> Tuple[str, str, bool]:
    """Normalize one edit to an (old_string, new_string, replace_all) triple."""
    if isinstance(edit, (tuple, list)):
        old_str, new_str = edit
        return old_str, new_str, False
    if isinstance(edit, Mapping):
        old_str = edit.get("old_string", edit.get("oldString"))
        new_str = edit.get("new_string", edit.get("newString"))
        replace_all = bool(edit.get("replace_all", edit.get("replaceAll", False)))
        if not isinstance(old_str, str):
            raise ValueError("Each edit must have a string old_string.")
        if not isinstance(new_str, str):
            raise ValueError("Each edit must have a string new_string.")
        return old_str, new_str, replace_all
    raise ValueError("Each edit must be an (old_string, new_string) tuple or dict.")


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
            raise ValueError(
                f"Edit {i}: old_string equals new_string "
                f"(no-op, oldString and newString must be different)."
            )

        if not old_str:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string must not be empty — skipped."
                )
                continue
            raise ValueError(f"Edit {i}: old_string must not be empty.")

        occurrences = evolving.count(old_str)
        if occurrences == 0:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string not found in content — skipped."
                )
                continue
            raise ValueError(f"Edit {i}: old_string not found in content.")

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
            raise ValueError(
                f"Edit {i}: Found multiple matches for old_string "
                f"({occurrences} occurrences, expected exactly 1). "
                f"Provide more surrounding lines to narrow it to a unique "
                f"block, or set replace_all=True to replace all occurrences."
            )

        evolving = evolving.replace(old_str, new_str) if replace_all else evolving.replace(old_str, new_str, 1)
        summary.updated.append(i)

    if not summary.updated:
        summary.warnings.append("No valid edits to apply.")
        return summary

    if evolving == current_source:
        return summary

    await write_like_multi_edit(studio, target_path, evolving)
    return summary


async def update_like_multi_edit(
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
        raise ValueError(
            f"File-system paths are not supported for update_like_multi_edit; "
            f"use write_like_multi_edit instead. Got: {target_path!r}"
        )

    # Read current source first (like Edit requires Read before Edit).
    result = await studio.script_read(target_path)
    current_source = _strip_line_prefixes(result.text())

    normalized = [_normalize_edit(e) for e in edits]

    # When any edit opts into replace-all, apply the batch sequentially in
    # memory and write the final body once.  The raw multi_edit requires
    # exactly one match per old_string, so it cannot express replace-all.
    if any(replace_all for _, _, replace_all in normalized):
        return await _apply_sequentially(
            studio, target_path, current_source, normalized,
            skip_missing=skip_missing, skip_no_ops=skip_no_ops,
        )

    # Classify each edit against the current source.
    valid_edits: List[Dict[str, Any]] = []
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
                raise ValueError(
                    f"Edit {i}: old_string equals new_string "
                    f"(no-op, oldString and newString must be different)."
                )

        if not old_str:
            summary.skipped_no_match.append(i)
            if skip_missing:
                summary.warnings.append(
                    f"Edit {i}: old_string must not be empty — skipped."
                )
                continue
            else:
                raise ValueError(f"Edit {i}: old_string must not be empty.")

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
                raise ValueError(f"Edit {i}: old_string not found in content.")

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
                raise ValueError(
                    f"Edit {i}: Found multiple matches for old_string "
                    f"({occurrences} occurrences, expected exactly 1). "
                    f"Provide more surrounding lines to narrow it to a unique "
                    f"block, or set replace_all=True to replace all occurrences."
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
