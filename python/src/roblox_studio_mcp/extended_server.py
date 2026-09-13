"""Extended stdio MCP server that wraps the Studio MCP with write/update tools.

This server is a thin layer over the standard Studio MCP proxy that adds
convenience tools on top of the raw Studio MCP tools (see _EXTENDED_TOOLS):

* ``extended_write_like_multi_edit`` — full-body script replacement (write semantics)
* ``extended_update_like_multi_edit`` — batch edits with graceful skipping + warnings

The server connects to a running Studio instance via the standard Studio MCP
proxy, then exposes the extended tools alongside the raw ones.  Claude Code (or
any MCP client) points at this server instead of the base one.

Launch it as a stdio server::

    python -m roblox_studio_mcp.extended_server

In Claude Code's settings.json, point the MCP server at this module::

    "mcpServers": {
        "roblox-studio-extended": {
            "command": "python",
            "args": ["-m", "roblox_studio_mcp.extended_server"]
        }
    }
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from .client import MCPClient
from .roblox import RobloxStudio, default_args, default_command, default_shell
from .types import CallToolResult, Tool

from .extended import RobloxStudio as _RobloxStudio
from .extended.writer import write_like_multi_edit as _write
from .extended.updater import update_like_multi_edit as _update, UpdateResult


# --------------------------------------------------------------------------- #
# Extended tool definitions (for tools/list)
# --------------------------------------------------------------------------- #

_EXTENDED_TOOLS: List[Tool] = [
    Tool(
        name="extended_write_like_multi_edit",
        description=(
            "RECOMMENDED over raw multi_edit for full-file writes.  Replaces a "
            "game-tree script's entire body with Claude Code Write semantics: "
            "reads the current source first, writes atomically, and returns "
            '"unchanged" without writing when the content is already identical.  '
            'Game-tree paths only (must start with "game.").  Set '
            "create_if_missing=true to create a missing script (otherwise a "
            "missing script is an error — new files are never created "
            'implicitly).  Returns a status string "wrote", "unchanged", or '
            '"created".'
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": (
                        'DataModel dot-path (e.g. game.ServerScriptService.MyScript).  '
                        'Must start with "game.".'
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "The new full text of the script.",
                },
                "className": {
                    "type": "string",
                    "default": "Script",
                    "description": (
                        "Roblox class when creating a new script (Script, "
                        "LocalScript, ModuleScript).  Only used with "
                        "create_if_missing.  Default: Script."
                    ),
                },
                "create_if_missing": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, create the script via execute_luau when it "
                        "doesn't exist yet.  If false (default), a missing "
                        "script is an error."
                    ),
                },
                "studio_id": {
                    "type": "string",
                    "description": (
                        "Optional explicit studio_id to target.  "
                        "Auto-detected if omitted."
                    ),
                },
            },
            "required": ["target_path", "content"],
        },
    ),
    Tool(
        name="extended_update_like_multi_edit",
        description=(
            "RECOMMENDED over raw multi_edit for targeted edits.  Applies a "
            "batch of exact string-replacement edits to a game-tree script with "
            "Claude Code Edit semantics: reads the current source first, applies "
            "edits in order (each operating on the result of the previous edit), "
            "and requires each old_string to match exactly once — like Edit, an "
            "old_string that is missing fails with an old_string-not-found error "
            "and one matching multiple regions fails as ambiguous unless that "
            "edit sets replace_all=true (like Edit's replaceAll).  By default "
            "missing/ambiguous/no-op edits are skipped with warnings and "
            "everything else is applied; set skip_missing=false or "
            "skip_no_ops=false for strict Edit-like failure.  Returns a "
            "structured summary of what was updated, skipped, and any warnings."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": (
                        'DataModel dot-path (e.g. game.ServerScriptService.MyScript).  '
                        'Must start with "game.".'
                    ),
                },
                "edits": {
                    "type": "array",
                    "description": (
                        "List of edit objects.  Edits are applied in order; each "
                        "operates on the result of the previous edit.  Edits whose "
                        "old_string is not found or matches new_string are skipped "
                        "with a warning by default."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {
                                "type": "string",
                                "description": (
                                    "Exact text to match (must appear exactly once "
                                    "unless replace_all is true)."
                                ),
                            },
                            "new_string": {
                                "type": "string",
                                "description": (
                                    "Replacement text (must differ from old_string)."
                                ),
                            },
                            "replace_all": {
                                "type": "boolean",
                                "default": False,
                                "description": (
                                    "Like Edit's replaceAll: replace every occurrence "
                                    "of old_string instead of requiring exactly one match."
                                ),
                            },
                            "replaceAll": {
                                "type": "boolean",
                                "default": False,
                                "description": "Alias of replace_all.",
                            },
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
                "skip_missing": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "If true (default), skip edits whose old_string is missing "
                        "or ambiguous.  If false, raise an Edit-like error instead."
                    ),
                },
                "skip_no_ops": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "If true (default), skip edits where old_string == "
                        "new_string.  If false, raise an error instead."
                    ),
                },
                "studio_id": {
                    "type": "string",
                    "description": (
                        "Optional explicit studio_id to target.  "
                        "Auto-detected if omitted."
                    ),
                },
            },
            "required": ["target_path", "edits"],
        },
    ),
]


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def _require_str(arguments: Dict[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string argument: {field!r}.")
    return value


async def _call_extended_write(
    client: MCPClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    target_path: str = _require_str(arguments, "target_path")
    content: str = arguments.get("content", "")
    if not isinstance(content, str):
        raise ValueError("Missing required string argument: 'content'.")
    class_name: Optional[str] = arguments.get("className")
    create_if_missing: bool = arguments.get("create_if_missing", False)
    studio_id: Optional[str] = arguments.get("studio_id")

    studio = _RobloxStudio(client=client, studio_id=studio_id)
    try:
        status = await _write(
            studio,
            target_path,
            content,
            className=class_name,
            create_if_missing=create_if_missing,
        )
    finally:
        # Don't close the shared client here — it's owned by the server.
        pass

    return {"content": [{"type": "text", "text": status}], "isError": False, "is_error": False}


async def _call_extended_update(
    client: MCPClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    target_path: str = _require_str(arguments, "target_path")
    edits_raw = arguments.get("edits")
    if not isinstance(edits_raw, list) or not edits_raw:
        raise ValueError("Missing required argument: 'edits' must be a non-empty list.")
    skip_missing: bool = arguments.get("skip_missing", True)
    skip_no_ops: bool = arguments.get("skip_no_ops", True)
    studio_id: Optional[str] = arguments.get("studio_id")

    edits: Sequence = []
    for e in edits_raw:
        if not isinstance(e, dict):
            raise ValueError("Each edit must be {old_string, new_string}.")
        old_string = e.get("old_string", e.get("oldString"))
        new_string = e.get("new_string", e.get("newString"))
        if not isinstance(old_string, str) or not old_string:
            raise ValueError("Each edit must have a non-empty string {old_string}.")
        if not isinstance(new_string, str):
            raise ValueError("Each edit must have a string {new_string}.")
        replace_all = bool(e.get("replace_all", e.get("replaceAll", False)))
        edits.append(
            {"old_string": old_string, "new_string": new_string, "replace_all": replace_all}
        )

    studio = _RobloxStudio(client=client, studio_id=studio_id)
    try:
        result: UpdateResult = await _update(
            studio,
            target_path,
            edits,
            skip_missing=skip_missing,
            skip_no_ops=skip_no_ops,
        )
    finally:
        # Don't close the shared client here — it's owned by the server.
        pass

    text = str(result)
    if result.warnings:
        text += "\n" + "\n".join(f"  WARNING: {w}" for w in result.warnings)

    return {"content": [{"type": "text", "text": text}], "isError": False, "is_error": False}


# --------------------------------------------------------------------------------- #
# Additional extended tools: search_and_read, insert_asset_from_file, watch_output,
# create_module_with_deps, run_tests
# --------------------------------------------------------------------------------- #

_EXTENDED_TOOLS.extend(
    [
        Tool(
            name="extended_script_search_and_read",
            description=(
                "Search for scripts under a DataModel path, then batch-read their "
                "sources.  Returns a JSON array of {path, source, name, line_count, "
                "truncated} entries.  Sources are truncated to max_chars_per_source "
                "(default 2000) with truncated=true; pass 0 for full sources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "root_path": {
                        "type": "string",
                        "description": "DataModel path (e.g. game.ServerScriptService).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional keyword filter for script names.",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 10,
                        "description": "Max number of results to return.",
                    },
                    "max_chars_per_source": {
                        "type": "integer",
                        "default": 2000,
                        "description": (
                            "Max characters of source per entry.  Longer sources "
                            "are truncated with truncated=true.  Pass 0 for full sources."
                        ),
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
                "required": ["root_path"],
            },
        ),
        Tool(
            name="extended_insert_asset_from_file",
            description=(
                "Insert a local file into the game tree.  "
                "file_type='image': uploads via store_image then insert_asset.  "
                "file_type='script': reads the file and creates a Script/LocalScript/ModuleScript "
                "at parent_path.asset_name using write_like_multi_edit."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Local file path."},
                    "file_type": {
                        "type": "string",
                        "enum": ["image", "script"],
                        "default": "script",
                        "description": "Type of file: 'image' (png/jpg/jpeg) or 'script' (luau/lua).",
                    },
                    "asset_name": {
                        "type": "string",
                        "description": "Name to give the inserted instance (defaults to file basename).",
                    },
                    "parent_path": {
                        "type": "string",
                        "default": "game.Workspace",
                        "description": "Container path in the DataModel (e.g. game.ReplicatedStorage).",
                    },
                    "className": {
                        "type": "string",
                        "default": "Script",
                        "description": (
                            "Roblox class for script files (Script, LocalScript, ModuleScript). "
                            "Only used when file_type='script'. Default: Script."
                        ),
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="extended_watch_output",
            description=(
                "Poll the Studio console and return only new lines since the last "
                "call.  Useful for live-tailing output during play testing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
            },
        ),
        Tool(
            name="extended_create_module_with_deps",
            description=(
                "Create a new ModuleScript and optionally add a require() statement "
                "to a target script.  Returns the write status."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "module_path": {
                        "type": "string",
                        "description": "DataModel path for the new module.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Source code for the module.",
                    },
                    "className": {
                        "type": "string",
                        "default": "ModuleScript",
                        "description": "Roblox class (default: ModuleScript).",
                    },
                    "require_target_path": {
                        "type": "string",
                        "description": "Optional script to inject require() into.",
                    },
                    "require_statement": {
                        "type": "string",
                        "description": "The require() line to add to the target.",
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
                "required": ["module_path", "content"],
            },
        ),
        Tool(
            name="extended_run_tests",
            description=(
                "Run play testing and collect console output as a test summary.  "
                "Returns {passed, console_lines, errors}."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "test_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of test script paths.",
                    },
                    "wait_seconds": {
                        "type": "number",
                        "default": 2.0,
                        "description": "How long to let play-mode output accumulate.",
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
            },
        ),
        Tool(
            name="extended_execute_luau_from_file",
            description=(
                "Execute Luau source read from a local file.  Same as "
                "execute_luau, but the code comes from a .luau/.lua file on "
                "disk instead of an inline string — useful for long scripts "
                "kept under version control.  Returns the execution result."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Local .luau/.lua file to execute.",
                    },
                    "datamodel_type": {
                        "type": "string",
                        "default": "Edit",
                        "description": "DataModel to run in (Edit, Client, Server).",
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
                "required": ["file_path"],
            },
        ),
    ]
)


async def _call_script_search_read(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.extensions import script_search_and_read

    root_path: str = _require_str(arguments, "root_path")
    query: Optional[str] = arguments.get("query")
    max_results: int = arguments.get("max_results", 10)
    max_chars_raw = arguments.get("max_chars_per_source", arguments.get("maxCharsPerSource", 2000))
    try:
        max_chars_per_source = int(max_chars_raw)
    except (TypeError, ValueError):
        max_chars_per_source = 2000
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    results = await script_search_and_read(
        studio,
        root_path,
        query=query,
        max_results=max_results,
        max_chars_per_source=max_chars_per_source,
    )
    return {
        "content": [{"type": "text", "text": json.dumps(results, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_insert_asset(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.extensions import insert_asset_from_file

    file_path: str = _require_str(arguments, "file_path")
    file_type: str = arguments.get("file_type", "script")
    asset_name: Optional[str] = arguments.get("asset_name")
    parent_path: str = arguments.get("parent_path", "game.Workspace")
    className: str = arguments.get("className", "Script")
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    result = await insert_asset_from_file(
        studio, file_path, file_type=file_type, asset_name=asset_name,
        parent_path=parent_path, className=className,
    )
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_watch_output(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.extensions import get_watch_state, watch_output

    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    # Persistent per-studio state so consecutive calls return only new lines.
    result = await watch_output(studio, watch_state=get_watch_state(studio_id or "default"))
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_create_module(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.extensions import create_module_with_deps

    module_path: str = _require_str(arguments, "module_path")
    content: str = arguments.get("content", "")
    if not isinstance(content, str):
        raise ValueError("Missing required string argument: 'content'.")
    class_name: str = arguments.get("className", "ModuleScript")
    require_target: Optional[str] = arguments.get("require_target_path")
    require_stmt: Optional[str] = arguments.get("require_statement")
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    status = await create_module_with_deps(
        studio,
        module_path,
        content,
        className=class_name,
        require_target_path=require_target,
        require_statement=require_stmt,
    )
    return {"content": [{"type": "text", "text": status}], "isError": False, "is_error": False}


async def _call_run_tests(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.extensions import run_tests

    test_paths: Optional[List[str]] = arguments.get("test_paths")
    wait_seconds: float = float(arguments.get("wait_seconds", 2.0))
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    result = await run_tests(studio, test_paths=test_paths, wait_seconds=wait_seconds)
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_execute_file(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.extensions import execute_luau_from_file

    file_path: str = _require_str(arguments, "file_path")
    datamodel_type: str = arguments.get("datamodel_type", "Edit")
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    result = await execute_luau_from_file(
        studio, file_path, datamodel_type=datamodel_type
    )
    return {"content": [{"type": "text", "text": result.text()}], "isError": False, "is_error": False}


_EXTENDED_HANDLERS = {
    "extended_write_like_multi_edit": _call_extended_write,
    "extended_update_like_multi_edit": _call_extended_update,
    "extended_script_search_and_read": _call_script_search_read,
    "extended_insert_asset_from_file": _call_insert_asset,
    "extended_watch_output": _call_watch_output,
    "extended_create_module_with_deps": _call_create_module,
    "extended_run_tests": _call_run_tests,
    "extended_execute_luau_from_file": _call_execute_file,
}


# --------------------------------------------------------------------------- #
# JSON-RPC relay loop (matches the existing server.py style)
# --------------------------------------------------------------------------- #

def _send(message: Dict[str, Any]) -> None:
    sys.stdout.buffer.write(
        (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
    )
    sys.stdout.buffer.flush()


async def _handle_message(
    client: MCPClient, message: Dict[str, Any]
) -> None:
    if "method" not in message:
        return

    method = message.get("method")
    has_id = "id" in message
    params = message.get("params") or {}

    if method == "initialize":
        if has_id:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": client.protocol_version,
                        "capabilities": client.capabilities,
                        "serverInfo": client.server_info,
                    },
                }
            )
        return

    if method in ("notifications/initialized", "notifications/cancelled"):
        return

    if method == "ping":
        if has_id:
            _send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
        return

    if method == "tools/list":
        if not has_id:
            return
        try:
            base = await client.request("tools/list", params)
        except Exception as exc:  # noqa: BLE001
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32000, "message": str(exc)},
                }
            )
            return
        # Steer callers toward the extended tools: annotate the raw
        # multi_edit so models prefer extended_write_like_multi_edit
        # (full-file writes) and extended_update_like_multi_edit
        # (targeted edits).
        base_tools = base.get("tools") or []
        annotated = []
        for t in base_tools:
            if isinstance(t, dict) and t.get("name") == "multi_edit":
                t = {
                    **t,
                    "description": (
                        f"{t.get('description') or 'Apply string-replacement edits to a script.'} "
                        "NOTE: prefer extended_write_like_multi_edit for full-file "
                        "replacement and extended_update_like_multi_edit for targeted "
                        "string replacements — they read first, enforce exact/unique "
                        "matching like Claude Code Edit/Write, and report what changed."
                    ),
                }
            annotated.append(t)
        base_tools = annotated
        extended_dicts = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in _EXTENDED_TOOLS
        ]
        _send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": base_tools + extended_dicts},
            }
        )
        return

    if method == "tools/call":
        if not has_id:
            return
        name = (params or {}).get("name")
        arguments = (params or {}).get("arguments") or {}

        if name in _EXTENDED_HANDLERS:
            try:
                result = await _EXTENDED_HANDLERS[name](client, arguments)
            except Exception as exc:  # noqa: BLE001
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32000, "message": str(exc)},
                    }
                )
                return
            _send(
                {"jsonrpc": "2.0", "id": message["id"], "result": result}
            )
            return

        # Pass through to the underlying Studio MCP.
        try:
            result = await client.request("tools/call", params)
        except Exception as exc:  # noqa: BLE001
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32000, "message": str(exc)},
                }
            )
            return
        _send(
            {"jsonrpc": "2.0", "id": message["id"], "result": result}
        )
        return

    if has_id:
        _send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
        )


async def serve(client: Optional[MCPClient] = None) -> None:
    """Run the extended stdio proxy until stdin closes."""
    owns_client = client is None
    if client is None:
        client = MCPClient(default_command(), default_args(), shell=default_shell())
        await client.connect()

    try:
        loop = asyncio.get_running_loop()
        stdin = sys.stdin.buffer
        while True:
            raw = await loop.run_in_executor(None, stdin.readline)
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            await _handle_message(client, message)
    finally:
        if owns_client:
            await client.close()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
