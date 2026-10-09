"""Extended Roblox Studio MCP — additional useful extensions.

Each extension provides a convenience wrapper over one or more raw Studio MCP
tools.  They are registered in extended_server.py as `extended_*` tools.

Usage (direct Python):

    from roblox_studio_mcp.extended import RobloxStudio
    from roblox_studio_mcp.extended.extensions import script_search_and_read

    async with await RobloxStudio.connect() as studio:
        results = await script_search_and_read(studio, "game.ServerScriptService", query="*Script*")
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from .writer import _strip_line_prefixes, _pick_bracket_level, _lua_long_bracket, validate_game_tree_path
from .errors import (
    CAPABILITY_DENIED,
    INVALID_ARGUMENT,
    SIZE_LIMIT,
    ToolError,
    describe,
)
from ..roblox import RobloxStudio
from ..types import CallToolResult


_SCRIPT_CLASSES = frozenset({"Script", "LocalScript", "ModuleScript"})

#: Default per-source truncation for script_search_and_read (0 = full source).
DEFAULT_MAX_CHARS_PER_SOURCE = 2000


# --------------------------------------------------------------------------- #
# Path confinement, shared by every tool that reads a caller-named file.
#
# These tools run at the caller's full user privilege and take a path straight
# from the model. Without a root, any user-readable file is readable -- so the
# root is the working directory, which still refuses `..` traversal and symlinks
# that point out while leaving the tool useful on a file outside any fixed tree.
#
# `resolve()` rather than `os.path.abspath` is the load-bearing choice: resolve
# follows symlinks to their real target, abspath only collapses `..`. A symlink
# inside the root pointing at ~/.ssh/id_rsa passes an abspath check untouched.
# That is the whole bug this replaces.
# --------------------------------------------------------------------------- #

#: Read a file named by the caller. Default root is the working directory.
#:
#: Sized from :data:`MEASURED_MAX_FILE_BYTES` below, with ~15% headroom --
#: generous enough not to break a real call, tight enough that a runaway read
#: fails instead of emptying memory.
MAX_FILE_BYTES = 16 * 1024 * 1024

#: The largest legitimate file measured on the machine that chose the cap above.
#:
#: Deliberately a separate constant rather than a literal inside the test that
#: checks the cap clears it. With the number in the test, anyone tightening
#: :data:`MAX_FILE_BYTES` could lower the literal in the same commit and satisfy
#: the check -- the test could not fail, because the fact it asserted was
#: editable from the same place as the thing it constrained. Here the two live
#: side by side and one edit cannot move both.
#:
#: Measured 2026-10-04 over 18,508 ``.luau``/``.lua``/``.rbxm``/``.rbxmx`` files
#: under this repo and its parent: the largest is 13,917,476 bytes
#: (``DataModelPatch.rbxm``, shipped with Studio), and the largest a caller would
#: realistically send is a 12,220,210-byte ``.luau``. Both are real inputs, so
#: the cap must clear them.
MEASURED_MAX_FILE_BYTES = 13_917_476


def _confined(file_path: str, *, allow_outside: bool = False) -> Path:
    """Resolve ``file_path`` and refuse anything outside the working directory.

    Args:
        file_path: The caller-supplied path. Not trusted.
        allow_outside: Explicit opt-out. Default is confined; the escape hatch
            exists so a deliberate whole-filesystem read is a choice rather than
            a workaround, not so confinement can be skipped by accident.

    Returns:
        The resolved absolute path, guaranteed inside the root unless
        ``allow_outside`` was passed.

    Raises:
        ToolError: ``CAPABILITY_DENIED`` when the resolved path is outside the
            root, or when it does not exist. Deliberately the same code for
            both: a caller learns the path is not usable, not whether it exists
            outside the root, which would make this an existence oracle.
    """
    root = Path.cwd().resolve()
    try:
        resolved = Path(file_path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        # RuntimeError: a symlink loop resolves forever otherwise.
        raise ToolError(
            INVALID_ARGUMENT,
            f"file_path could not be resolved: {exc}. "
            f"Check for a symlink loop or an unreadable parent directory.",
        ) from exc

    if allow_outside:
        return resolved

    if not resolved.is_relative_to(root):
        raise ToolError(
            CAPABILITY_DENIED,
            f"file_path is outside the working directory. {describe(file_path)} "
            f"resolves to {describe(str(resolved))}, which is outside "
            f"{describe(str(root))}. Pass a path inside the working directory, or "
            f"set allow_outside=true if you really mean to read it.",
            path=str(resolved),
            root=str(root),
        )

    if resolved.exists() and not resolved.is_file():
        # Only once the path is known to exist, so a *missing* file still falls
        # through to the caller's existence check as INVALID_ARGUMENT. Confinement
        # answers "where may this point", not "is there anything there".
        raise ToolError(
            CAPABILITY_DENIED,
            f"file_path is inside the working directory but is not a file: "
            f"{describe(str(resolved))}. These tools read files; directories and "
            f"special files are refused.",
            path=str(resolved),
        )

    if not resolved.exists():
        return resolved

    size = resolved.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError(
            SIZE_LIMIT,
            f"file is {size:,} bytes, over the {MAX_FILE_BYTES:,}-byte limit for "
            f"this tool. Split it, or narrow the path to part of it.",
            size=size,
            limit=MAX_FILE_BYTES,
        )

    return resolved


async def _read_one_script(
    studio: RobloxStudio,
    dot_path: str,
    name: Any,
    max_chars: Optional[int],
) -> Optional[Dict[str, Any]]:
    try:
        src_result = await studio.script_read(target_file=dot_path)
    except Exception:
        return None  # Skip unreadable scripts; caller truncates to max_results.
    full = _strip_line_prefixes(src_result.text())
    line_count = 0 if full == "" else len(full.split("\n"))
    source = full
    truncated = False
    if max_chars is not None and max_chars > 0 and len(full) > max_chars:
        source = full[:max_chars]
        truncated = True
    return {
        "path": dot_path,
        "source": source,
        "name": name,
        "line_count": line_count,
        "truncated": truncated,
    }


async def script_search_and_read(
    studio: RobloxStudio,
    root_path: str,
    query: Optional[str] = None,
    max_results: int = 10,
    max_chars_per_source: Optional[int] = DEFAULT_MAX_CHARS_PER_SOURCE,
) -> List[Dict[str, Any]]:
    """Search for scripts under root_path, then batch-read each source.

    Returns a list of dicts: [{"path": ..., "source": ..., "name": ...,
    "line_count": ..., "truncated": ...}]. Sources are truncated to
    ``max_chars_per_source`` (default 2000) to keep payloads small;
    ``line_count`` always reflects the full source. Pass 0 or None for full
    sources. Unreadable scripts are skipped. Returns [] when the tree payload
    cannot be parsed (e.g. unexpected server format).

    **Omitting ``query`` means no name filter, and that is the fix.** This used
    to pass ``keywords="Script"`` when ``query`` was None, so the *optional*
    parameter defaulted to a filter: an agent auditing ``ServerScriptService``
    for its scripts got back only the ones whose names happened to contain
    "Script", with no indication anything was excluded. Every legitimate script
    not matching that substring vanished from an audit with zero signal - found
    by hostile fuzzing, which asked for "everything under this path" and got a
    partial answer that looked complete.

    An empty ``keywords`` is what the underlying tool takes for "no filter",
    which is what the schema already promised by making the parameter optional.

    Shape faults fail here, before any Studio call: a ``root_path`` that is
    not a game-tree path raises ``INVALID_ARGUMENT`` rather than returning
    whatever the tree search happens to do with it, and a non-integer or
    out-of-range ``max_results`` raises too. ``[]`` therefore means "the query
    was well-formed and nothing matched" (or the payload was unparseable) -
    with one known blind spot: a well-formed path that names nothing that
    exists also returns [], because non-existence is not decidable without
    the round trip whose error shape is unverified without live Studio.
    """
    import json as _json

    from .grep import _bounded_int

    validate_game_tree_path(root_path, field="root_path")
    max_results = _bounded_int("max_results", max_results, 1, 100)

    # Use search_game_tree to find scripts (requires datamodel_type for Edit mode).
    tree_result = await studio.search_game_tree(
        path=root_path,
        datamodel_type="Edit",
        keywords=query or "",
    )
    try:
        tree_data = _json.loads(tree_result.text())
    except (ValueError, TypeError):
        return []
    # Some servers wrap the list in a dict (e.g. {"instances": [...]}).
    if isinstance(tree_data, dict):
        for key in ("instances", "data", "result", "children"):
            value = tree_data.get(key)
            if isinstance(value, list):
                tree_data = value
                break
    if not isinstance(tree_data, list):
        return []

    candidates: List[tuple[str, Any]] = []
    for item in tree_data:
        if not isinstance(item, dict):
            continue
        if item.get("className") not in _SCRIPT_CLASSES:
            continue
        full_path = item.get("fullPath")
        if full_path:
            candidates.append((full_path, item.get("name")))
        if len(candidates) >= max_results:
            break

    # Batch-read concurrently (bounded) instead of one round-trip at a time.
    sem = asyncio.Semaphore(5)

    async def _bounded(path: str, name: Any) -> Optional[Dict[str, Any]]:
        async with sem:
            return await _read_one_script(studio, path, name, max_chars_per_source)

    gathered = await asyncio.gather(*(_bounded(p, n) for p, n in candidates))
    return [r for r in gathered if r is not None]


async def insert_asset_from_file(
    studio: RobloxStudio,
    file_path: str,
    file_type: str = "script",
    asset_name: Optional[str] = None,
    parent_path: str = "game.Workspace",
    className: str = "Script",
    allow_outside: bool = False,
) -> Dict[str, Any]:
    """Insert a local file into the game tree.

    ``file_path`` is confined to the current working directory. A path outside it
    -- by ``..`` or through a symlink pointing out -- is refused with
    ``CAPABILITY_DENIED`` unless ``allow_outside`` is set. Files over
    ``MAX_FILE_BYTES`` are refused with ``SIZE_LIMIT``.

    Args:
        file_type: One of "script" (luau/lua), "model" (rbxm/rbxmx), or
            "image" (png/jpg/jpeg).  Order of priority: script, model, image.
        asset_name: Name to give the inserted instance (defaults to file basename).
        parent_path: Container path in the DataModel (e.g. game.ReplicatedStorage).
        className: Roblox class for script files (Script, LocalScript, ModuleScript).
        allow_outside: Read a path outside the working directory. Off by default.

    Returns:
        Dict with "status" and relevant fields.
    """
    import os
    import re

    # Rebind rather than keep a second name: every later read in this function
    # uses `file_path`, and there is deliberately no `resolved` local left to
    # disagree with it. The value is the confined one, so `isfile`, `basename`,
    # `open` and the `store_image` call all see the same absolute path with no
    # second expansion anywhere.
    file_path = str(_confined(file_path, allow_outside=allow_outside))

    if not os.path.isfile(file_path):
        # INVALID_ARGUMENT, deliberately not NOT_FOUND and deliberately not a
        # FileNotFoundError. This used to classify as NOT_FOUND purely because
        # the message contained "not found", but NOT_FOUND in this vocabulary
        # means "the thing you named is not in the DataModel" - the recovery
        # there is to re-list the DataModel, which is useless when the miss is
        # on the host filesystem and the DataModel was never consulted. The
        # recovery here is to send a path that exists, which is an argument
        # fault like any other. Raising the code rather than hoping `classify`
        # guesses right also stops a reworded message from silently moving the
        # code.
        raise ToolError(
            INVALID_ARGUMENT,
            f"file_path must name an existing local file, got {describe(file_path)}. "

            f"Read the path back before sending it.",
        )
    if parent_path:
        validate_game_tree_path(parent_path, field="parent_path")

    file_type = file_type.lower()
    target_name = asset_name or os.path.splitext(os.path.basename(file_path))[0]
    if not target_name:
        raise ToolError(
            INVALID_ARGUMENT,
            f"Could not derive asset_name from file_path {describe(file_path)}. "
            f"Pass asset_name explicitly.",
        )

    if file_type == "image":
        # Step 1: Upload via store_image
        try:
            store_result = await studio.call("store_image", {"filePath": file_path})
            store_text = store_result.text()
        except Exception as e:
            return {
                "status": "store_image_failed",
                "file_path": file_path,
                "note": f"store_image call failed: {str(e)[:200]}",
            }

        match = re.search(r"IMAGEID_([A-Za-z0-9_-]+)", store_text)
        if not match:
            return {
                "status": "upload_parsing_failed",
                "file_path": file_path,
                "note": f"Could not parse image ID from store_image response: {store_text[:200]}",
            }

        image_id = match.group(1)
        insert_kwargs: Dict[str, Any] = {
            "assetId": image_id,
            "assetType": "Image",
        }
        if asset_name:
            insert_kwargs["assetName"] = asset_name
        if parent_path:
            insert_kwargs["parentPath"] = parent_path

        try:
            insert_result = await studio.call("insert_asset", insert_kwargs)
            return {
                "status": "inserted",
                "file_path": file_path,
                "image_id": image_id,
                "asset_name": asset_name,
                "parent_path": parent_path,
                "result": insert_result.text(),
            }
        except Exception as e:
            return {
                "status": "insert_failed",
                "file_path": file_path,
                "image_id": image_id,
                "note": f"insert_asset failed: {str(e)[:300]}. "
                        "May require asset in inventory or Studio MCP permissions.",
            }

    if file_type == "script":
        with open(file_path, "r", encoding="utf-8", newline=None) as f:
            # Universal newlines: CRLF files produce identical Sources
            # regardless of which client imports them.
            content = f.read().replace("\r\n", "\n").replace("\r", "\n")

        # Determine the target path in the game tree
        target_path = f"{parent_path.rstrip('.')}.{target_name}" if parent_path else target_name

        from .writer import write_script
        status = await write_script(
            studio,
            target_path,
            content,
            className=className,
            create_if_missing=True,
        )
        return {
            "status": status,
            "file_path": file_path,
            "target_path": target_path,
            "asset_name": target_name,
            "parent_path": parent_path,
            "className": className,
        }

    if file_type == "model":
        # Import WITHOUT LoadLocalAsset (needs RobloxScript/plugin security
        # the Assistant's execute_luau lacks).  Instead: base64 the bytes,
        # `write` slice 0 into a SINGLE scratch ModuleScript, append the rest
        # via ScriptEditorService:UpdateSourceAsync, then in-Studio read
        # .Source -> EncodingService:Base64Decode -> buffer ->
        # SerializationService:DeserializeInstancesAsync -> parent_path.
        # Proven against live Studio (651KB binary .rbxm + 6.7MB XML .rbxmx).
        import base64

        from .writer import write_script

        # Scratch lives in PluginGuiService: studio-only, never replicates or
        # publishes with the place (unlike ServerStorage).
        _MODEL_SLICE_CHARS = 180 * 1024
        _SCRATCH_NAME = "RBXImportPayload"
        _SCRATCH_PATH = f"game.PluginGuiService.{_SCRATCH_NAME}"
        try:
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            return {
                "status": "insert_failed",
                "file_path": file_path,
                "note": f"model load failed: could not read file: {str(e)[:200]}",
            }
        slices = [b64[i:i + _MODEL_SLICE_CHARS] for i in range(0, len(b64), _MODEL_SLICE_CHARS)]

        try:
            await studio.execute_luau(
                f"local old = game.PluginGuiService:FindFirstChild({_SCRATCH_NAME!r})\n"
                "if old then old:Destroy() end\n"
                "return \"scratch clean\"\n",
                datamodel_type="Edit",
            )
            await write_script(
                studio, _SCRATCH_PATH, slices[0],
                className="ModuleScript", create_if_missing=True,
            )
            for i in range(1, len(slices)):
                lit = _lua_long_bracket(slices[i], _pick_bracket_level(slices[i]))
                await studio.execute_luau(
                    f"local target = {_SCRATCH_PATH}\n"
                    f"local slice = {lit}\n"
                    "game:GetService(\"ScriptEditorService\"):UpdateSourceAsync"
                    "(target, function(old) return old .. slice end)\n"
                    "return \"appended \" .. #slice\n",
                    datamodel_type="Edit",
                )
            name_lit = _lua_long_bracket(target_name, _pick_bracket_level(target_name))
            assemble = (
                f"local target = {_SCRATCH_PATH}\n"
                "local data = target.Source\n"
                "local raw = game:GetService(\"EncodingService\"):Base64Decode(buffer.fromstring(data))\n"
                "local instances = game:GetService(\"SerializationService\"):DeserializeInstancesAsync(raw)\n"
                "assert(#instances > 0, \"deserialized zero instances\")\n"
                f"if #instances == 1 then instances[1].Name = {name_lit} end\n"
                f"local parent = {parent_path}\n"
                "local names = {}\n"
                "for _, inst in ipairs(instances) do\n"
                "  inst.Parent = parent\n"
                "  table.insert(names, inst.ClassName .. \":\" .. inst.Name)\n"
                "end\n"
                "target:Destroy()\n"
                "return \"imported \" .. #instances .. \" root(s): \" .. table.concat(names, \", \")\n"
            )
            result = await studio.execute_luau(assemble, datamodel_type="Edit")
            return {
                "status": "inserted",
                "file_path": file_path,
                "asset_name": target_name,
                "parent_path": parent_path,
                "result": result.text(),
            }
        except Exception as e:
            return {
                "status": "insert_failed",
                "file_path": file_path,
                "note": f"model load failed: {str(e)[:300]}. "
                        "May require file path accessible to Studio.",
            }

    return {
        "status": "unsupported_file_type",
        "file_path": file_path,
        "note": f"file_type '{file_type}' is not supported. Supported: script, model, image",
    }


class WatchResult(TypedDict):
    """What :meth:`_ConsoleWatch.poll` actually returns.

    This exists because of a shipped bug. ``extended_watch_output`` read
    ``result["text"]`` while this dict is keyed ``new_lines`` / ``total_lines`` /
    ``last_line``; ``.get("text", "")`` supplied the default, the tool returned
    ``{"returned": 0}`` on **every** call, and it reported ``isError: false``.

    The honest limit, measured: with the return type as bare
    ``Dict[str, Any]`` no type checker can see it, and with a ``TypedDict``
    **neither mypy nor pyright flags the ``.get()`` form** - a defaulted
    ``.get`` on a missing key is legal by design in both. What a TypedDict *does*
    catch is the subscript form, and what it does for this codebase is make the
    three real keys visible in one place. See
    ``python/scripts/typecheck_acid_test.py`` for the measurement, and
    ``TODO.md`` for the conclusion that the annotation is the fix and the checker
    is a backstop.
    """

    new_lines: List[str]
    total_lines: int
    last_line: Optional[str]


class _ConsoleWatch:
    """Stateful watch that remembers console output between polls.

    Assumes logs are append-only. Handles duplicate lines by matching on the
    last occurrence, and log truncation/rotation by falling back to the full
    current buffer.
    """

    _FIRST_CALL_TAIL = 20
    _MAX_NEW_LINES = 200

    def __init__(self):
        self._last_lines: List[str] = []

    def _diff(self, current: List[str]) -> List[str]:
        previous = self._last_lines
        if not previous:
            return current[-self._FIRST_CALL_TAIL:]
        # Fast path: previous buffer is an exact prefix of current (append-only).
        if len(current) >= len(previous) and current[: len(previous)] == previous:
            return current[len(previous):]
        # Slow path: find the last occurrence of the previous tail to tolerate
        # duplicate lines. If not found, logs were truncated — return all.
        last = previous[-1]
        for i in range(len(current) - 1, -1, -1):
            if current[i] == last:
                # Prefer the occurrence that leaves a plausible tail; take the last one.
                return current[i + 1 :]
        return list(current)

    async def poll(self, studio: RobloxStudio) -> WatchResult:
        result = await studio.call("get_console_output", {})
        lines = result.text().splitlines()

        new_lines = self._diff(lines)[-self._MAX_NEW_LINES:]
        self._last_lines = lines
        return {
            "new_lines": new_lines,
            "total_lines": len(lines),
            "last_line": lines[-1] if lines else None,
        }


# Persistent watch states keyed by studio target, so the MCP server tool
# (which gets a fresh call each time) still returns only *new* lines.
_WATCH_STATES: Dict[str, _ConsoleWatch] = {}


def get_watch_state(key: str = "default") -> _ConsoleWatch:
    """Return (creating if needed) the persistent watch state for ``key``."""
    watch = _WATCH_STATES.get(key)
    if watch is None:
        watch = _ConsoleWatch()
        _WATCH_STATES[key] = watch
    return watch


async def watch_output(
    studio: RobloxStudio,
    watch_state: Optional[_ConsoleWatch] = None,
) -> WatchResult:
    """Poll console output and return only new lines since last call.

    When ``watch_state`` is omitted a process-wide default is reused so
    consecutive calls only return fresh lines. Pass an explicit
    :class:`_ConsoleWatch` for isolated/per-studio tracking.
    """
    if watch_state is None:
        key = getattr(studio, "studio_id", None) or "default"
        watch_state = get_watch_state(str(key))
    return await watch_state.poll(studio)


_ERROR_HINTS = ("error", "failed", "stack trace", "exception")


def _is_error_line(line: str) -> bool:
    lowered = line.lower()
    # Ignore benign "0 errors" / "0 failed" summaries.
    if "0 error" in lowered or "0 fail" in lowered:
        return False
    return any(hint in lowered for hint in _ERROR_HINTS)


async def run_tests(
    studio: RobloxStudio,
    test_paths: Optional[List[str]] = None,
    wait_seconds: float = 2.0,
    max_lines: int = 30,
) -> Dict[str, Any]:
    """Run play testing and collect output as a test summary.

    Args:
        test_paths: Optional script paths to verify exist before playing.
        wait_seconds: How long to let play-mode output accumulate.
        max_lines: Max console lines returned in the summary.

    Returns {passed: bool, console_lines: [...], errors: [...]}
    """
    # The loop below runs until output stabilises *or the budget runs out*,
    # so an unbounded budget is an unbounded loop: `1e9` only terminates if
    # the console happens to go quiet, and NaN never satisfies `>=` at all.
    # Cap it at the client's own timeout horizon; anything above is a fault.
    if (
        isinstance(wait_seconds, bool)
        or not isinstance(wait_seconds, (int, float))
        or not 0 <= wait_seconds <= 120
    ):
        raise ToolError(
            INVALID_ARGUMENT,
            f"wait_seconds must be a number between 0 and 120, got "
            f"{describe(wait_seconds)}.",
        )
    missing: List[str] = []
    if test_paths:
        for path in test_paths:
            try:
                await studio.script_read(path)
            except Exception as exc:
                missing.append(f"{path}: {exc}")

    await studio.start_play()
    try:
        # Poll until output stabilises or the budget runs out, instead of a
        # single fixed sleep (flaky on slow places).
        console_text = ""
        interval = 0.5
        elapsed = 0.0
        last_snapshot = ""
        while True:
            await asyncio.sleep(min(interval, max(0.0, wait_seconds - elapsed)))
            elapsed += interval
            console_result = await studio.call("get_console_output", {})
            console_text = console_result.text()
            if console_text == last_snapshot or elapsed >= wait_seconds:
                break
            last_snapshot = console_text
        lines = console_text.splitlines() if console_text else []
        errors = [line for line in lines if _is_error_line(line)]
        passed = not errors and not missing
        return {
            "passed": passed,
            "console_lines": lines[-max_lines:] if lines else [],
            "errors": errors + ([f"Missing test script: {m}" for m in missing] if missing else []),
        }
    finally:
        await studio.stop_play()


async def execute_luau_from_file(
    studio: RobloxStudio,
    file_path: str,
    datamodel_type: str = "Edit",
    encoding: str = "utf-8",
    allow_outside: bool = False,
) -> CallToolResult:
    """Execute Luau source read from a local file.

    Same as :meth:`RobloxStudio.execute_luau`, but the code comes from a
    ``.luau``/``.lua`` file on disk instead of an inline string — useful for
    long scripts kept under version control or generated by other tools.

    ``file_path`` is confined to the current working directory. A path outside it
    -- by ``..`` or through a symlink pointing out -- is refused with
    ``CAPABILITY_DENIED`` unless ``allow_outside`` is set. Files over
    ``MAX_FILE_BYTES`` are refused with ``SIZE_LIMIT``.

    Args:
        studio: A connected :class:`RobloxStudio` (use the singleton).
        file_path: Local file to read, inside the working directory. ``~`` is
            expanded first, then the result must land back inside the root --
            expanding ``~`` alone is not an escape hatch.
        datamodel_type: DataModel to run in (``"Edit"``, ``"Client"``,
            ``"Server"``).
        encoding: File encoding (default UTF-8).
        allow_outside: Read a path outside the working directory. Off by default.

    Raises:
        ToolError: ``CAPABILITY_DENIED`` if ``file_path`` resolves outside the
            working directory or is not a regular file; ``SIZE_LIMIT`` if it is
            over ``MAX_FILE_BYTES``; ``INVALID_ARGUMENT`` if it does not exist
            inside the root, or names an empty file. The latter are the caller's
            argument rather than a DataModel miss, so neither is ``NOT_FOUND``.

    Returns:
        The execution result from Studio.
    """
    import os

    # Confine first, then check existence -- so a path outside the root is
    # refused as outside rather than probed for existence.
    resolved = str(_confined(file_path, allow_outside=allow_outside))
    if not os.path.isfile(resolved):
        # INVALID_ARGUMENT rather than NOT_FOUND: see the same decision in
        # `insert_asset_from_file`. The DataModel was never consulted here.
        raise ToolError(
            INVALID_ARGUMENT,
            f"file_path must name an existing local file, got {describe(resolved)}.",

        )
    with open(resolved, "r", encoding=encoding, newline=None) as f:
        # Universal newlines, so one written file reads back identically.
        code = f.read().replace("\r\n", "\n").replace("\r", "\n")
    if not code.strip():
        raise ToolError(
            INVALID_ARGUMENT,
            f"file_path names an empty file: {describe(resolved)}. "

            f"Write the Luau to it before executing.",
        )
    return _annotate_array_escape(
          await studio.execute_luau(code, datamodel_type=datamodel_type)
      )


#: What Studio's serialiser does to an array, measured live. See
#: :func:`_array_escape_paths`.
ARRAY_ESCAPE_NOTE = (
      "Array-shaped values do not survive this transport. Studio stringifies "
      "integer keys, so a Luau array {10,20,30} arrives as an object with keys "
      "\"1\",\"2\",\"3\" rather than [10,20,30], and Vector2.new(3,4) arrives as "
      "the single string \"3, 4\". Reported at: %s. This is a NOTE and nothing "
      "was rewritten: a table with genuine string keys \"1\",\"2\" produces the "
      "identical bytes, so repairing it would corrupt real data. Serialise at the "
      "source - HttpService:JSONEncode - and read the string, which survives "
      "intact."
  )


def _array_escape_paths(value: Any, path: str = "") -> List[str]:
      """Where a Luau array most likely arrived as a string-keyed object.

      Measured live against a real Studio, 2026-09-30:

      ==========================  =========================================
      Luau returned                arrived as
      ==========================  =========================================
      ``{10,20,30}``               ``{"1":10,"2":20,"3":30}``
      ``{rows={{n=1,v=10},...}}``  ``{"rows":{"1":{...},"2":{...}}}``
      ``{p=Vector2.new(3,4)}``     ``{"p":"3, 4"}``
      ``JSONEncode({10,20,30})``   ``{"json":"[10,20,30]"}``  - intact
      ==========================  =========================================

      The signature is a dict whose keys are exactly ``"1".."n"`` with ``n >= 2``
      and nothing else. **This cannot be repaired, only reported** - and that is
      the point of returning a note rather than fixing it:

      * ``{[1]=x,[2]=y}`` is a genuine Luau **array** and should have been a list
      * ``{["1"]=x,["2"]=y}`` is a genuine **string-keyed map** and is already
        correct as ``{"1":x,"2":y}``

      Both serialise to the same bytes. Any heuristic that "fixes" the first
      silently corrupts the second, and any hard failure fires on the second. So
      the only honest move is to say the shape is present and let the caller
      decide at the source, where the distinction still exists.

      The originating report is ``REQUEST-luau-return-shapes.md`` in this repo,
      where a 165-row driver returned ``{}`` with no error because of exactly
      this - invisible without a control, and one step from a false finding
      about the engine.
      """
      found: List[str] = []
      if isinstance(value, dict):
          # `str(k)` before the digit test, not after. A dict built in-process
          # can have real `int` keys - `{1: x, 2: y}` - and `k.isdigit()` on an
          # int is an AttributeError that escaped this function and would have
          # failed the whole tool call. Over JSON the keys are always strings, so
          # only an in-process caller reaches it; that is still a caller.
          keys = [str(k) for k in value]
          if len(keys) >= 2 and all(k.isdigit() for k in keys):
              numbers = sorted(int(k) for k in keys)
              if numbers == list(range(1, len(numbers) + 1)):
                  found.append(path or "(root)")
          for key, item in value.items():
              found.extend(_array_escape_paths(item, "%s.%s" % (path, key) if path else str(key)))
      elif isinstance(value, list):
          for index, item in enumerate(value):
              found.extend(_array_escape_paths(item, "%s[%d]" % (path, index)))
      return found


def _annotate_array_escape(result: CallToolResult) -> CallToolResult:
      """Append the array-escape note when the result shows the shape. Never edits it.

      Additive on purpose. The payload the caller reads is untouched, because the
      two candidate shapes are indistinguishable and any rewrite is a coin flip.

      The whole body is guarded, not just the parse. A diagnostic that can fail
      the call it is annotating is worse than no diagnostic, and an earlier draft
      guarded only ``json()`` - which is exactly how the integer-key crash below
      got as far as escaping.
      """
      try:
          parsed = result.json()
          if not isinstance(parsed, (dict, list)):
              return result
          paths = _array_escape_paths(parsed)
      except Exception:  # noqa: BLE001 - see above
          return result
      if not paths:
          return result
      shown = ", ".join(paths[:5])
      if len(paths) > 5:
          shown += " (+%d more)" % (len(paths) - 5)
      result.content.append(
          {"type": "text", "text": ARRAY_ESCAPE_NOTE % shown}
      )
      return result
