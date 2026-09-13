"""Write-like wrapper around `multi_edit` for game-tree scripts.

Mirrors Claude Code Write semantics for game-tree scripts.  The Studio MCP's
``multi_edit`` is great for targeted string replacements, but sometimes you
just want to replace a script's *entire* body — the ``open(path, "w")``
equivalent for game scripts.  This module provides
:func:`write_like_multi_edit`:

* Read first: the current source is always fetched via ``script_read``
  before writing (like Write requires Read before overwriting).
* Atomic: the whole body is replaced in a single ``multi_edit`` call.
* Idempotent: re-running with identical content is a safe no-op.
* Never create implicitly: a missing script is only created when
  ``create_if_missing`` is explicitly set (like Write never creates new
  files unless asked).  Otherwise the missing-script error propagates.
* Game-tree only: ``target_path`` must start with ``"game."`` (a DataModel
  dot-path).  File-system paths are not accepted — use plain
  :func:`pathlib.Path.write_text` for that.
* Chunked: for content over the 200K ``Script.Source`` limit, uses an
  append-slice pattern (like ``ScriptEditorService:UpdateSourceAsync``)
  to build the source up incrementally.
"""

from __future__ import annotations

import re
from typing import Optional

from ..roblox import RobloxStudio


_GAME_TREE_PREFIX = "game."

# Roblox engine hard limit on Script.Source (and any string property).
# Beyond this, direct assignment fails; use the append-slice pattern instead.
_STRING_PROPERTY_SIZE_LIMIT = 200_000

_VALID_SCRIPT_CLASSES = frozenset({"Script", "LocalScript", "ModuleScript"})

# ``script_read`` prefixes each line as ``<line-number>→<content>`` (e.g. ``12→print(1)``).
# Only strip that leading pattern so a literal ``→`` inside the source survives.
_LINE_PREFIX_RE = re.compile(r"^\s*\d+\s*→")

# Heuristic substrings indicating script_read failed because the script is missing
# (vs. a connection/permission failure that must not be masked as "missing").
_MISSING_HINTS = (
    "not found",
    "not exist",
    "no such",
    "could not find",
    "couldn't find",
    "does not exist",
    "doesn't exist",
    "missing",
    "unknown",
    "nil",
)

_MAX_CHUNKED_RETRIES = 5


def _strip_line_prefixes(text: str) -> str:
    """Remove ``LINE_NUMBER→`` prefixes returned by ``script_read``."""
    return "\n".join(_LINE_PREFIX_RE.sub("", line, count=1) for line in text.splitlines())


def _validate_class_name(className: str) -> str:
    if className not in _VALID_SCRIPT_CLASSES:
        raise ValueError(
            f"className must be one of {sorted(_VALID_SCRIPT_CLASSES)}; got {className!r}."
        )
    return className


def _pick_bracket_level(*parts: str, start: int = 10) -> int:
    """Pick a ``[===[...]===]`` level whose closer appears in none of ``parts``.

    Long-bracket strings are raw — no escapes needed — so we just bump the
    ``=`` count until ``]===]`` can't collide with the payload.
    """
    level = start
    while True:
        closer = f"]{'=' * level}]"
        if any(closer in p for p in parts):
            level += 1
            continue
        return level


def _lua_long_bracket(value: str, level: int) -> str:
    """Wrap ``value`` as a raw Lua long-bracket string (no escapes needed).

    A leading newline is guarded: Lua skips the first character of a
    long-bracket string when it is a newline, so values starting with
    ``"\\n"``/``"\\r"`` get one extra newline that Lua consumes, preserving
    the payload exactly. This matters for chunked slices, which may start
    anywhere — including right after a newline.
    """
    eq = "=" * level
    guard = "\n" if value.startswith(("\n", "\r")) else ""
    return f"[{eq}[{guard}{value}]{eq}]"


def _is_missing_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _MISSING_HINTS)


def _split_target(target_path: str) -> tuple[str, str]:
    parts = target_path.split(".")
    if len(parts) < 2:
        raise ValueError(
            f"target_path must be a DataModel dot-path like "
            f"'game.ServerScriptService.MyScript'; got {target_path!r}."
        )
    return ".".join(parts[:-1]), parts[-1]


def _build_chunked_lua(
    container: str,
    name: str,
    className: str,
    slices: list[str],
    level: int,
) -> str:
    """Build the Lua script for chunked writing with given bracket level.

    Slices are embedded as raw long-bracket strings — no escapes needed.
    ``name`` uses the same raw form so quotes/backslashes can't break it.
    """
    slice_entries = [_lua_long_bracket(s, level) for s in slices]
    slices_table = "{" + ",".join(slice_entries) + "}"
    name_lit = _lua_long_bracket(name, level)
    return (
        f'local slices = {slices_table}\n'
        f'local ss = game:GetService("ScriptEditorService")\n'
        f'local target = {container}:FindFirstChild({name_lit})\n'
        f'if not target then\n'
        f'    target = Instance.new("{className}")\n'
        f'    target.Name = {name_lit}\n'
        f'    target.Parent = {container}\n'
        f'    target.Source = ""\n'
        f'end\n'
        f'for i = 1, #slices do\n'
        f'    local slice = slices[i]\n'
        # Capture the index: UpdateSourceAsync callbacks may run after the
        # loop finishes, when shared `i` would read as #slices + 1 for all
        # of them (appending instead of replacing on slice 1).
        f'    local idx = i\n'
        f'    ss:UpdateSourceAsync(target, function(old)\n'
        f'        if idx == 1 then\n'
        f'            return slice\n'
        f'        else\n'
        f'            return old .. slice\n'
        f'        end\n'
        f'    end)\n'
        f'end\n'
    )


async def _chunked_write(
    studio: RobloxStudio,
    target_path: str,
    content: str,
    *,
    className: str = "Script",
) -> None:
    """Write content >200K via a single execute_luau with slice loop.

    Uses ``ScriptEditorService:UpdateSourceAsync`` inside Studio in a loop,
    passing each 200K slice individually.  Studio handles the accumulation
    internally and bypasses the 200K Source assignment limit.
    """
    slices = [
        content[i:i + _STRING_PROPERTY_SIZE_LIMIT]
        for i in range(0, len(content), _STRING_PROPERTY_SIZE_LIMIT)
    ]

    if not slices:
        return

    _validate_class_name(className)
    container, name = _split_target(target_path)

    # Pick a collision-free level upfront (raw long-brackets need no escapes);
    # keep the parse-retry as a safety net for exotic Studio parsing quirks.
    level = _pick_bracket_level(name, *slices)
    for attempt in range(_MAX_CHUNKED_RETRIES + 1):
        lua_script = _build_chunked_lua(
            container, name, className, slices, level
        )
        try:
            await studio.execute_luau(lua_script, datamodel_type="Edit")
            return
        except Exception as exc:
            if "parse" in str(exc).lower() and attempt < _MAX_CHUNKED_RETRIES:
                level += 1
                continue
            raise
    raise RuntimeError("Chunked write failed: Lua long-bracket collision persists.")


async def write_like_multi_edit(
    studio: RobloxStudio,
    target_path: str,
    content: str,
    *,
    className: Optional[str] = None,
    create_if_missing: bool = False,
    return_string: bool = False,
) -> str:
    """Atomically replace a script's contents with Claude Code ``Write`` semantics.

    Reads the current source first; returns ``"unchanged"`` without writing
    when the content is already identical.  Creates the script only when
    ``create_if_missing`` is true.

    Returns a status message: ``"wrote"``, ``"unchanged"``, or ``"created"``.

    Parameters
    ----------
    studio
        An initialised :class:`RobloxStudio` instance.
    target_path
        A DataModel dot-path (must start with ``"game."``, e.g.
        ``"game.ServerScriptService.MyScript"``).
    content
        The new full text of the script.
    className
        When *create_if_missing* is true and the script doesn't exist, the
        Roblox class to use (e.g. ``"Script"``, ``"LocalScript"``,
        ``"ModuleScript"``).  Defaults to ``"Script"``.
    create_if_missing
        If true and the game-tree script doesn't exist, create it first.
    return_string
        If true, wrap ``content`` as ``return "<content>"`` and force
        ``className`` to ``"ModuleScript"`` so callers can ``require`` it.
    """
    if return_string:
        className = "ModuleScript"
        # Raw long-bracket return — no escapes needed.
        level = _pick_bracket_level(content)
        content = f"return {_lua_long_bracket(content, level)}"

    if not target_path.startswith(_GAME_TREE_PREFIX):
        raise ValueError(
            f"target_path must start with {_GAME_TREE_PREFIX!r} (game-tree path); "
            f"got {target_path!r}. Use a DataModel dot-path like "
            f"'game.ServerScriptService.MyScript'."
        )

    className = _validate_class_name(className or "Script")

    try:
        result = await studio.script_read(target_path)
        current_source = _strip_line_prefixes(result.text())
    except Exception as exc:
        if not create_if_missing:
            raise
        # Don't mask connection/permission failures as "missing".
        if not _is_missing_error(exc):
            raise
        container, name = _split_target(target_path)

        if len(content) > _STRING_PROPERTY_SIZE_LIMIT:
            # For large content, create an EMPTY script first, then chunked-write.
            # Can't assign Source directly in execute_luau due to 200K limit.
            level = _pick_bracket_level(name)
            name_lit = _lua_long_bracket(name, level)
            await studio.execute_luau(
                f"local parent = {container}\n"
                f'local newScript = Instance.new("{className}")\n'
                f"newScript.Name = {name_lit}\n"
                f"newScript.Source = ''\n"
                f"newScript.Parent = parent\n",
                datamodel_type="Edit",
            )
            await _chunked_write(studio, target_path, content, className=className)
            return "created"

        # Small create: raw long-brackets for both Name and Source — no escapes needed.
        level = _pick_bracket_level(name, content)
        name_lit = _lua_long_bracket(name, level)
        source_lit = _lua_long_bracket(content, level)
        await studio.execute_luau(
            (
                f"local parent = {container}\n"
                f'local newScript = Instance.new("{className}")\n'
                f"newScript.Name = {name_lit}\n"
                f"newScript.Source = {source_lit}\n"
                f"newScript.Parent = parent\n"
            ),
            datamodel_type="Edit",
        )
        return "created"

    if current_source == content:
        return "unchanged"

    if len(content) > _STRING_PROPERTY_SIZE_LIMIT:
        await _chunked_write(studio, target_path, content, className=className)
        return "wrote"

    await studio.call(
        "multi_edit",
        {
            "file_path": target_path,
            "datamodel_type": "Edit",
            "edits": [{"old_string": current_source, "new_string": content}],
        },
    )
    return "wrote"
