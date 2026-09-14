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
from typing import Any, Dict, List, Optional, Sequence

from .writer import _GAME_TREE_PREFIX, _strip_line_prefixes
from .updater import UpdateResult
from ..roblox import RobloxStudio
from ..types import CallToolResult


_SCRIPT_CLASSES = frozenset({"Script", "LocalScript", "ModuleScript"})

#: Default per-source truncation for script_search_and_read (0 = full source).
DEFAULT_MAX_CHARS_PER_SOURCE = 2000


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
    """
    import json as _json

    # Use search_game_tree to find scripts (requires datamodel_type for Edit mode).
    tree_result = await studio.search_game_tree(
        path=root_path,
        datamodel_type="Edit",
        keywords="Script" if query is None else query,
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
) -> Dict[str, Any]:
    """Insert a local file into the game tree.

    Args:
        file_type: One of "image" (png/jpg/jpeg) or "script" (luau/lua).
        asset_name: Name to give the inserted instance (defaults to file basename).
        parent_path: Container path in the DataModel (e.g. game.ReplicatedStorage).
        className: Roblox class for script files (Script, LocalScript, ModuleScript).

    Returns:
        Dict with "status" and relevant fields.
    """
    import os
    import re

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Local file not found: {file_path}")
    if parent_path and not parent_path.startswith(_GAME_TREE_PREFIX):
        raise ValueError(
            f"parent_path must be a game-tree path starting with {_GAME_TREE_PREFIX!r}; "
            f"got {parent_path!r}."
        )

    file_type = file_type.lower()
    target_name = asset_name or os.path.splitext(os.path.basename(file_path))[0]
    if not target_name:
        raise ValueError(f"Could not derive asset name from file_path {file_path!r}.")

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

        from .writer import write_like_multi_edit
        status = await write_like_multi_edit(
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

    return {
        "status": "unsupported_file_type",
        "file_path": file_path,
        "note": f"file_type '{file_type}' is not supported. Supported: image, script",
    }


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

    async def poll(self, studio: RobloxStudio) -> Dict[str, Any]:
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
) -> Dict[str, Any]:
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
) -> CallToolResult:
    """Execute Luau source read from a local file.

    Same as :meth:`RobloxStudio.execute_luau`, but the code comes from a
    ``.luau``/``.lua`` file on disk instead of an inline string — useful for
    long scripts kept under version control or generated by other tools.

    Args:
        studio: A connected :class:`RobloxStudio` (use the singleton).
        file_path: Local file to read. ``~`` is expanded.
        datamodel_type: DataModel to run in (``"Edit"``, ``"Client"``,
            ``"Server"``).
        encoding: File encoding (default UTF-8).

    Raises:
        FileNotFoundError: If ``file_path`` does not exist.
        ValueError: If the file is empty.

    Returns:
        The execution result from Studio.
    """
    import os

    resolved = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"Luau file not found: {resolved}")
    with open(resolved, "r", encoding=encoding, newline=None) as f:
        # Universal newlines, matching the TypeScript client.
        code = f.read().replace("\r\n", "\n").replace("\r", "\n")
    if not code.strip():
        raise ValueError(f"Luau file is empty: {resolved}")
    return await studio.execute_luau(code, datamodel_type=datamodel_type)
