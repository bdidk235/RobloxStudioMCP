from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, TypedDict

from ..roblox import RobloxStudio
from ..types import _extract_balanced
from .errors import INVALID_ARGUMENT, ToolError, describe
from .writer import _strip_line_prefixes


def _bounded_int(name: str, value: object, low: int, high: int) -> int:
    """An integer argument, checked for both type and range.

    The type check is not pedantry. Node's equivalent used ``Number(...)``, so
    a non-numeric string became ``NaN``; every comparison against ``NaN`` is
    false, so ``NaN < 1`` and ``NaN > 100`` both pass and the cap was applied
    as ``hits[:NaN]``. The same input was rejected on Python and silently
    accepted on Node, which is the divergence this now closes.
    """
    # bool is an int subclass, and `max_results: true` is not a count.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(
            INVALID_ARGUMENT,
            f"{name} must be an integer, got {describe(value)}.",
        )
    if not low <= value <= high:
        raise ToolError(
            INVALID_ARGUMENT,
            f"{name} must be between {low} and {high}, got {value}.",
        )
    return value


#: One hit line from ``script_grep``'s **text** reply, captured from a live call:
#:
#:     Path: game.ServerScriptService.UsabilityProbe | Line: 2 | print('PROBE')
#:
#: Anchored, and the path is non-greedy so a ``|`` inside the *content* cannot
#: swallow the ``Line:`` field. Measured content in this format does contain
#: pipes, which is why splitting on the first two separators is not enough.
_GREP_TEXT_RE = re.compile(
    r"^Path:\s*(?P<path>.+?)\s*\|\s*Line:\s*(?P<line>\d+)\s*\|\s?(?P<content>.*)$"
)


def _parse_grep_text(text: str) -> List[Dict[str, Any]]:
    """Parse the plain-text reply format ``script_grep`` actually returns.

    **This was missing, and it made the tool return an empty list with a success
    status for text that provably existed** — the worst failure shape in this
    project, because an agent searching for something it had just written was told
    it was not there.

    Live evidence, 2026-09-30, on Studio ``0_186489``:

        Path: PluginDebugService.…Highlighter.lexer | Line: 131 | … print(…)
        Path: PluginDebugService.…RbxDom.database | Line: 44069 | … EnableSprinting = {
        … Search stopped after reaching the limit of 50 matches.

    Two details that are load-bearing:

    - The trailing ``… Search stopped after reaching the limit of 50 matches.``
      is a **truncation notice, not a hit**, and does not match the pattern, so
      it is dropped. Reading it as a hit would report a file named after a
      sentence.
    - Content lines can start with tabs and contain quotes and pipes. Only the
      two structural separators are consumed; everything after the second is
      content.
    """
    hits: List[Dict[str, Any]] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        match = _GREP_TEXT_RE.match(line)
        if not match:
            continue
        hits.append(
            {
                "path": match.group("path"),
                "line_number": int(match.group("line")),
                "content": match.group("content"),
            }
        )
    return hits


def _as_hit_dicts(value: object) -> List[Dict[str, Any]]:
    """Coerce a JSON list into hit dicts, dropping anything that is not one.

    Added because the fallback in :func:`extended_script_grep` assigned a raw
    ``list[str]`` from ``raw_result.json()`` and the next line called
    ``hit.get("path")`` on a ``str`` — an unhandled ``AttributeError`` escaping
    the tool, with no error code and no recovery. Whatever shape arrives, a
    non-dict is dropped here rather than detonating three lines later.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _parse_grep_raw(text: str) -> List[Dict[str, Any]]:
    """Pull the hit list out of ``script_grep``'s reply.

    Three formats are attempted, because the relay has been observed returning
    more than one and a parser that only knows the first reports "no hits" for
    text that exists:

    1. A JSON array, possibly wrapped in balanced delimiters.
    2. A JSON object with a ``results`` list.
    3. **Plain text**, one hit per line — see :func:`_parse_grep_text`. This is
       what the relay returns today, and omitting it is what made this tool
       silently empty.

    Stays ``Dict[str, Any]`` on purpose. The keys genuinely vary by design — the
    aliases below (``path``/``target_file``/``name`` and
    ``line_number``/``line``/``lineNumber``) exist precisely because the server's
    field names are not fixed, and it is parsed from whatever came back. A
    TypedDict here would be a shape this module does not control. The *output*
    of :func:`extended_script_grep` is the fixed part, and that is typed.
    """
    raw = _extract_balanced(text.strip())
    if raw is not None:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("results"), list):
                return data["results"]
        except Exception:
            pass
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data["results"]
    except Exception:
        pass

    # Text format, last: the JSON attempts above are cheap and unambiguous, and
    # trying them first means a JSON reply is never mis-read as prose.
    return _parse_grep_text(text)


class GrepHit(TypedDict):
    """One enriched hit, as :func:`extended_script_grep` returns it.

    This is the tool's whole output and every key is always present, so the shape
    is fixed even though the *input* hit dicts are not (see :func:`_parse_grep_raw`).
    Declaring it is what keeps ``json.dumps(hits)`` in the server honest: four
    keys, no more, whatever the server sent.

    ``excerpt`` is ``str`` and that took a fix. It used to be built with
    ``hit.get("excerpt") or hit.get("line") or ...``, but ``line`` is *also* the
    first-choice alias for the line **number** two lines above, so a server that
    sent only ``{"line": 12}`` produced ``excerpt=12`` — an int in a field every
    consumer treats as text, and a hit with no source text at all. The two
    lookups are now separate: a number never becomes an excerpt.
    """

    path: str
    line_number: int
    excerpt: str
    context_lines: int


def _excerpt(source: str, line_no: int, context: int) -> str:
    lines = source.split("\n")
    i = max(0, line_no - 1 - context)
    j = min(len(lines), line_no + context)
    return "\n".join(lines[i:j])


async def extended_script_grep(
    studio: RobloxStudio,
    query: str,
    *,
    root_path: Optional[str] = None,
    context_lines: int = 3,
    regex: bool = False,
    instance_type: Optional[str] = None,
    max_results: int = 30,
) -> List[GrepHit]:
    # Every fault here is the caller's own argument, so every one carries
    # INVALID_ARGUMENT explicitly rather than relying on ``classify()`` to
    # recognise prose. The three range checks used to be bare ValueErrors that
    # no pattern matched, so an agent branching on the code was told "unknown"
    # about a fault that was unambiguously its own.
    if not isinstance(query, str) or not query.strip():
        raise ToolError(
            INVALID_ARGUMENT,
            f"query must be a non-empty string, got {describe(query)}.",
        )
    max_results = _bounded_int("max_results", max_results, 1, 100)
    context_lines = _bounded_int("context_lines", context_lines, 0, 10)
    if regex:
        # Compiled up front rather than mid-loop. The old site was inside the
        # `if not excerpt` branch, so an invalid regex was only reported when a
        # hit happened to lack an excerpt - the same input either errored or
        # silently worked depending on Studio's output.
        try:
            re.compile(query)
        except re.error as exc:
            raise ToolError(
                INVALID_ARGUMENT,
                f"query is not a valid regex (regex=true): {exc}",
            ) from None

    raw_kwargs: Dict[str, Any] = {"query": query}
    if root_path:
        raw_kwargs["root_path"] = root_path
    if instance_type:
        raw_kwargs["instance_type"] = instance_type

    raw_result = await studio.call("script_grep", raw_kwargs)
    hits = _as_hit_dicts(_parse_grep_raw(raw_result.text()))

    if not hits:
        try:
            hits = _as_hit_dicts(raw_result.json())
        except Exception:
            pass

    enriched: List[GrepHit] = []
    for hit in hits[:max_results]:
        path = hit.get("path") or hit.get("target_file") or hit.get("name") or ""
        if not isinstance(path, str) or not path:
            # `not path` alone is the real test; the isinstance is what lets the
            # type checker see that a non-string alias (a dict, a number) is
            # dropped here rather than reaching the output as `path`.
            continue
        line_no = hit.get("line_number") or hit.get("line") or hit.get("lineNumber") or 1
        try:
            line_no = int(line_no)
        except Exception:
            line_no = 1

        # `line` is deliberately NOT an excerpt alias. It is the first-choice
        # alias for the line *number* just above, so reading it as text too meant
        # a server sending only {"line": 12} put the int 12 in `excerpt` and,
        # because that is truthy, skipped the source read that would have
        # supplied the real text. The number is now the only thing `line` means.
        raw_excerpt = hit.get("excerpt") or hit.get("content")
        # Coerced in one step rather than via `or ""`, so a non-string excerpt
        # (a number, a nested object) is dropped here rather than typed as text.
        excerpt = raw_excerpt if isinstance(raw_excerpt, str) else ""
        if not excerpt:
            try:
                src = await studio.script_read(target_file=path)
                full = _strip_line_prefixes(src.text())
                excerpt = _excerpt(full, line_no, context_lines)
            except Exception:
                excerpt = ""

        enriched.append(
            {
                "path": path,
                "line_number": line_no,
                "excerpt": excerpt,
                "context_lines": context_lines,
            }
        )

    return enriched
