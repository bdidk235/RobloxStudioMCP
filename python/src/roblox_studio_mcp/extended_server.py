"""Extended stdio MCP server that wraps the Studio MCP with write/update tools.

This server is a thin layer over the standard Studio MCP proxy that adds
convenience tools on top of the raw Studio MCP tools (see _EXTENDED_TOOLS):

* ``extended_write_script`` — full-body script replacement (write semantics)
* ``extended_update_script`` — batch edits with graceful skipping + warnings
* ``extended_list_studios`` — Studio instances with a stable, host-registered
  identity (``GetDebugId``) so they stay traceable across proxy restarts

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
import re
import sys
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
)

from .client import MCPClient
from .extended.errors import ToolError, describe
from .roblox import RobloxStudio, default_args, default_command, default_shell
from .types import CallToolResult, Tool

from .extended import RobloxStudio as _RobloxStudio
from .extended.grep import extended_script_grep as _grep
from .extended.writer import write_script as _write
from .extended.updater import update_script as _update, UpdateResult


#: Relayed tools that have a better `extended_*` equivalent, and the steer
#: appended to each relayed description. Mirrored into `parity/tools.json` by
#: `parity/build_contract.py` and asserted by both suites, because a
#: hand-written copy on each side would drift - and the failure mode is a model
#: quietly choosing the worse tool, which is invisible because the worse tool
#: still works. See `parity/build_contract.py::_STEERS` for the measured reason
#: behind each entry.
#:
#: Why the relayed description rather than a skill: the model chooses a tool
#: while reading the tool list, which is the only always-on surface. A skill is
#: opt-in, so a trap living only there has already been walked into by the time
#: anyone reads it.
_STEERS: Dict[str, str] = {
    "multi_edit": (
        "NOTE: prefer extended_write_script for a full-file replacement, or "
        "extended_update_script for targeted edits - this fails the whole call "
        "if any one edit is invalid, and does not read the target first."
    ),
    "screen_capture": (
        "NOTE: prefer extended_capture when the pixels are the measurement - "
        "this is JPEG (smears 1px edges); extended_capture names the studio_id "
        "it captured and can save to a file instead of returning megabytes."
    ),
    "script_grep": (
        "NOTE: prefer extended_script_grep - this returns no context lines, so "
        "each hit costs a separate script_read to see."
    ),
    "get_console_output": (
        "NOTE: prefer extended_watch_output when polling - this returns the "
        "whole buffer every time, re-sending lines you already have."
    ),
    "script_search": (
        "NOTE: prefer extended_script_search_and_read to also batch-read the "
        "sources; this returns paths only."
    ),
}


# --------------------------------------------------------------------------- #
# Extended tool definitions (for tools/list)
# --------------------------------------------------------------------------- #

_EXTENDED_TOOLS: List[Tool] = [
    Tool(
        name="extended_write_script",
        description=(
            "Replace a game-tree script's whole body. Atomic, and returns "
            '"unchanged" without writing when the content already matches. '
            "Prefer over raw multi_edit, which fails the entire call if any one "
            "part is invalid."
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
        name="extended_update_script",
        description=(
            "Batch exact string replacements in a game-tree script, in order, "
            "each on the result of the last.  Unlike raw multi_edit, one bad "
            "edit does not sink the others: missing, ambiguous and no-op edits "
            "are skipped with warnings unless you make them strict."
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
    Tool(
        name="extended_script_grep",
        description=(
            "Search script contents with surrounding context, in file order (not "
            "ranked). Substring by default, regex with regex:true. Prefer over "
            "raw script_grep, which returns no context."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring or regex to grep for (depending on regex flag).",
                },
                "root_path": {
                    "type": "string",
                    "description": "Dot-path to start from (e.g. game.ServerScriptService).",
                },
                "context_lines": {
                    "type": "integer",
                    "default": 3,
                    "description": "Number of context lines around each hit (0-10, default 3).",
                },
                "regex": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, treat query as a Luau/PCRE regex.",
                },
                "instance_type": {
                    "type": "string",
                    "description": "Only match Luau containers of this Instance type.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 30,
                    "description": "Cap on returned hits (1-100, default 30).",
                },
                "studio_id": {
                    "type": "string",
                    "description": "Optional explicit studio_id to target.  Auto-detected if omitted.",
                },
            },
            "required": ["query"],
        },
    ),
]


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def _require_str(arguments: Dict[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value:
        # The received value is in the message because "must be a non-empty
        # string" alone cannot tell the caller whether it sent `""`, `42` or
        # `null`, and those three need three different fixes.
        raise ToolError(
            "INVALID_ARGUMENT",
            f"{field} must be a non-empty string, got {describe(value)}.",
        )
    return value


def _error_payload(exc: BaseException) -> Dict[str, Any]:
    """A JSON-RPC error carrying a stable code and structured data.

    ``-32000`` is kept as the numeric code because that is what the transport
    and existing clients expect; the stable string code lives in ``data.code``
    for callers that want to branch, and is duplicated at the top level of
    ``error`` for readability.
    """
    from .extended.errors import classify

    err = classify(exc)
    return {
        "code": -32000,
        "message": err.message,
        # Flat, and deliberately identical to Node's `toJsonRpcError`. Python
        # used to nest the tool-specific keys under `data.details` while Node
        # spread them, so a caller branching on `data.unknown` worked on Node
        # and read `undefined` on Python. A payload that is flat on one side and
        # nested on the other is the worst kind of divergence: it fails silently
        # on exactly the branch a caller wrote.
        "data": {"code": err.code, **err.data},
    }


async def _call_extended_write(
    client: MCPClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    target_path: str = _require_str(arguments, "target_path")
    content: str = arguments.get("content", "")
    if not isinstance(content, str):
        # Empty content is legal here - it blanks a script - so only the type
        # is checked, and the message says which type.
        raise ToolError(
            "INVALID_ARGUMENT",
            f"content must be a string, got {describe(content)}. "
            f"Use \"\" to blank a script.",
        )
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
        raise ToolError(
            "INVALID_ARGUMENT",
            f"edits must be a non-empty list, got {describe(edits_raw)}. "
            f"Send [{{\"old_string\": ..., \"new_string\": ...}}].",
        )
    skip_missing: bool = arguments.get("skip_missing", True)
    skip_no_ops: bool = arguments.get("skip_no_ops", True)
    studio_id: Optional[str] = arguments.get("studio_id")

    # Was annotated `Sequence`, which has no `append`. The annotation was wrong,
    # not the code: this genuinely is a growing list of edit dicts. Found by
    # type checking, and a good example of why - the mismatch was invisible to
    # every runtime path here.
    edits: List[Dict[str, Any]] = []
    for index, e in enumerate(edits_raw):
        # Each message names the offending index and shows the received value.
        # "Each edit must be {old_string, new_string}" is unactionable from a
        # batch of ten: the caller has to re-send to find out which one it was,
        # and `""` / `42` / absent all read the same.
        if not isinstance(e, dict):
            raise ToolError(
                "INVALID_ARGUMENT",
                f"edits[{index}] must be an object "
                f"{{old_string, new_string}}, got {describe(e)}.",
            )
        old_string = e.get("old_string", e.get("oldString"))
        new_string = e.get("new_string", e.get("newString"))
        if not isinstance(old_string, str) or not old_string:
            raise ToolError(
                "INVALID_ARGUMENT",
                f"edits[{index}].old_string must be a non-empty string, "
                f"got {describe(old_string)}.",
            )
        if not isinstance(new_string, str):
            raise ToolError(
                "INVALID_ARGUMENT",
                f"edits[{index}].new_string must be a string "
                f"(it may be \"\"), got {describe(new_string)}.",
            )
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
# run_tests
# --------------------------------------------------------------------------------- #

_EXTENDED_TOOLS.extend(
    [
        Tool(
            name="extended_script_search_and_read",
            description=(
                "Find scripts under a DataModel path and batch-read their "
                "sources. Sources truncate at max_chars_per_source, so pass 0 "
                "when you need them whole."
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
            description="Insert a local script, model, or image file into the game tree.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Local file path."},
                    "file_type": {
                        "type": "string",
                        "enum": ["script", "model", "image"],
                        "default": "script",
                        "description": "Type of file: 'script' (luau/lua), 'model' (rbxm/rbxmx), or 'image' (png/jpg/jpeg).",
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
                "New console lines since your last call, filtered by pattern and "
                "capped by max_lines, so you get what matters instead of the whole "
                "buffer.  Use this instead of get_console_output in a loop."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Only return lines matching this regex.",
                    },
                    "max_lines": {
                        "type": "integer",
                        "default": 200,
                        "description": "Cap on returned lines, keeping the newest.",
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
            },
        ),
        Tool(
            name="extended_run_tests",
            description=(
                "Play the place; console as {passed, console_lines, errors}. "
                "Green-check only. See rsx-playtest."
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
            name="extended_breakpoints",
            description=(
                "Set or remove one breakpoint on a running server script. Each "
                "hit prints one console line; read them with "
                "extended_watch_output.  log_expression MUST fail, or no hit is "
                "reported.  Toggles.  See the rsx-breakpoints skill."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": (
                            "Script name under ServerScriptService, e.g. 'BpTest'."
                        ),
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-based source line.",
                    },
                    "log_expression": {
                        "type": "string",
                        "description": (
                            "Luau expression run on every hit; defaults to a bare "
                            "hit marker.  See the rsx-breakpoints skill."
                        ),
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
                "required": ["script_path", "line"],
            },
        ),
        Tool(
            name="extended_clear_breakpoints",
            description=(
                "Remove every breakpoint and clear the registry.  Needs a play "
                "session.  See the rsx-breakpoints skill."
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
            name="extended_skill",
            description=(
                "Fetch a transport skill from the repo's skills/ folder; omit "
                "skill_name for the index.  Read one before driving the transport: "
                "these cover its traps, not the engine."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Skill to fetch. Omit to list what is available.",
                    },
                },
            },
        ),
        Tool(
            name="extended_manage_instance",
            description=(
                "Studio instances and places.  list -> mesh[].studio_id per "
                "attached Studio (unjoined: pair a process's place_file with a "
                "mesh name yourself).  launch -> the studio_id it opened, pass "
                "it on.  places -> candidate paths + place_id.  make_place -> "
                "a throwaway path to launch.  stop -> TERMINATES a process, "
                "irreversibly.  Launch needs a place; a Studio joins the mesh "
                "only with one open."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "launch", "stop", "places", "make_place"],
                        # No `default`. `action` is required, so a default could
                        # never apply - and advertising one tells the caller the
                        # argument is optional, which is the "default in
                        # disguise" this project rejects. Caught by
                        # `test_parity.py::test_no_required_argument_is_shadowed_by_a_default`.
                        "description": "What to do.",
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "For stop: which Studio to terminate.",
                    },
                    "place_path": {
                        "type": "string",
                        "description": "For launch: the place file to open.",
                    },
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="extended_wait_for",
            description=(
                "Wait until a Luau condition is true, polling host-side, then "
                "return a verdict with the last value.  Use it when a result "
                "takes time: you cannot sleep between calls.  Return true, "
                "false or nil; anything else errors - 0 is truthy in Lua."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "string",
                        "description": (
                            "Luau expression, e.g. "
                            "'#game:GetService(\"Players\"):GetPlayers() >= 3'.  "
                            "true, false or nil; anything else is an error."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "default": 30.0,
                        "description": "How long to wait. Capped at 90.",
                    },
                    "datamodel_type": {
                        "type": "string",
                        "default": "Edit",
                        "description": "DataModel to evaluate in (Edit, Client, Server).",
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
                "required": ["condition"],
            },
        ),
        Tool(
            name="extended_capture",
            description=(
                "Lossless PNG of the viewport, for when the pixels are the "
                "measurement.  screen_capture is JPEG-only and smears 1px edges: "
                "use it for a look, this to measure.  Costs seconds and megabytes.  "
                "See the rsx-capture skill."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": (
                            "Local path to write the PNG to.  Omit to receive the "
                            "PNG as base64 in the response instead, which is "
                            "large (roughly 1 KB per 1000 px of viewport)."
                        ),
                    },
                    "studio_id": {
                        "type": "string",
                        "description": "Optional explicit studio_id to target.",
                    },
                },
            },
        ),
        Tool(
            name="extended_studio_identity",
            description=(
                "This Studio's game:GetDebugId(). Separates two Studios on one "
                "place; does NOT survive a restart, so it is session-scoped.  "
                "Edit mode only.  See the rsx-targeting skill."
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
            name="extended_list_studios",
            description=(
                "List connected Studios, each with its current studio_id and "
                "debug_id.  Neither is durable: both change on restart, so this "
                "tracks a place, not an instance.  See the rsx-targeting skill."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Re-query Studio and re-read each GetDebugId.  Set "
                            "false to report the registry as-is, with no round trip."
                        ),
                    },
                    "resolve": {
                        "type": "object",
                        "description": (
                            "Look one registered instance up instead of listing.  "
                            "Give debug_id for an exact match, or name/place_id "
                            "together.  Ambiguous selectors return every candidate "
                            "rather than guessing."
                        ),
                        "properties": {
                            "debug_id": {"type": "string"},
                            "name": {"type": "string"},
                            "place_id": {"type": "number"},
                        },
                    },
                },
            },
        ),
        Tool(
            name="extended_execute_luau_from_file",
            description=(
                "execute_luau with the code read from a local .luau file, for "
                "anything too long to inline in a tool call."
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
    """New console lines since the last poll, optionally filtered.

    The filter is applied to the result of the existing watch call, never by
    re-invoking the tool. An earlier version called
    ``studio.call("extended_watch_output")`` through the underlying Studio
    client, which does not have that tool, and it failed with "Tool handler not
    found". The differential state lives here too, so routing through the raw
    client would have lost it as well.
    """
    from .extended.extensions import get_watch_state, watch_output

    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    # Persistent per-studio state so consecutive calls return only new lines.
    result = await watch_output(studio, watch_state=get_watch_state(studio_id or "default"))

    # `watch_output` returns `new_lines` as a real list, already split by the
    # watch, so there is nothing to split here.
    #
    # This previously read `result["text"]` - a key this dict does not have. Its
    # keys are `new_lines`, `total_lines` and `last_line`. `dict.get` returned the
    # `""` default, the split produced no lines, and the tool returned
    # `{"returned": 0, "total_seen": 0}` on **every call** while reporting
    # `isError: false`. Live confirmation: `total_seen: 0` with a real console
    # behind it. It survived because the only test on this tool asserted its
    # *description*.
    #
    # The literal-`\n` split is kept below for the raw-client shape, which is
    # the case the original comment was written for: the console arrives as ONE
    # line with literal escapes, so splitting on a real newline finds nothing.
    raw_lines = result.get("new_lines")
    if isinstance(raw_lines, list):
        lines = [str(ln) for ln in raw_lines if str(ln).strip()]
    else:
        lines = [ln for ln in str(result.get("text", "")).split("\\n") if ln.strip()]

    pattern = arguments.get("pattern")
    matched = lines
    if pattern:
        try:
            rx = re.compile(str(pattern))
        except re.error as exc:
            raise ToolError("INVALID_ARGUMENT", f"bad pattern: {exc}") from None
        matched = [ln for ln in lines if rx.search(ln)]

    # `or 200` would treat a requested 0 as absent, because 0 is falsy, and
    # silently return 200 lines to a caller who asked for none. An explicit
    # `is None` test is the difference between an honest default and a default
    # in disguise - which is the same defect request P0.2a is about, reached
    # from the other direction.
    raw_cap = arguments.get("max_lines")
    try:
        cap = 200 if raw_cap is None else int(raw_cap)
    except (TypeError, ValueError):
        raise ToolError(
            "INVALID_ARGUMENT", f"max_lines must be an integer, got {describe(raw_cap)}"
        ) from None
    if cap < 1:
        # Says what to send instead: 0 means "no lines" and is refused on
        # purpose, because the falsy-default bug it used to trigger returned
        # 200 lines to a caller who asked for none.
        raise ToolError(
            "INVALID_ARGUMENT",
            f"max_lines must be at least 1, got {describe(raw_cap)}. "
            f"Omit it for the default of 200.",
        )
    shown = matched[-cap:]

    return {
        "content": [{"type": "text", "text": json.dumps({
            "text": "\n".join(shown),
            "lines": shown,
            "returned": len(shown),
            "matched": len(matched),
            "total_seen": len(lines),
            "truncated": len(shown) < len(matched),
        }, indent=2)}],
        "isError": False, "is_error": False,
    }


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


async def _call_breakpoints(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """Set or remove one breakpoint. Listing lives in the clear/registry path."""
    from .extended.breakpoints import remove_breakpoint, set_breakpoint

    script_path = _require_str(arguments, "script_path")
    # Parsed here, once, with the received value in the error. `int("x")` used
    # to raise a bare ValueError that classified as UNKNOWN, and Node's
    # `Math.trunc(Number("x"))` produced NaN which reached Studio - the same
    # request behaved differently per server.
    raw_line = arguments.get("line")
    try:
        line = 0 if raw_line is None else int(raw_line)
    except (TypeError, ValueError):
        raise ToolError(
            "INVALID_ARGUMENT",
            f"line must be a 1-based positive integer, got {describe(raw_line)}.",
        ) from None
    studio = _RobloxStudio(client=client, studio_id=arguments.get("studio_id"))

    # No `action` argument: the registry tells us whether this location already
    # has a breakpoint, so the caller says what they want ("here") rather than
    # restating the state.  That is the whole reason this is a branch and not a
    # required enum.
    from .extended.breakpoints import list_breakpoints

    existing = [
        b for b in await list_breakpoints(studio)
        if b.get("script_path") == script_path and b.get("line") == line
    ]
    if existing:
        await remove_breakpoint(studio, script_path, line)
        result: Dict[str, Any] = {
            "removed": {"script_path": script_path, "line": line},
        }
    else:
        result = await set_breakpoint(
            studio, script_path, line,
            log_expression=arguments.get("log_expression"),
        )
        result = {"added": result}

    result["hit_prefix"] = "Breakpoint "
    result["how_to_read_hits"] = (
        "extended_watch_output with pattern='^Breakpoint '; each hit is one line."
    )
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_clear_breakpoints(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.breakpoints import clear_breakpoints

    studio = _RobloxStudio(client=client, studio_id=arguments.get("studio_id"))
    await clear_breakpoints(studio)
    return {
        "content": [{"type": "text", "text": json.dumps({"cleared": True}, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_wait_for(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.waiting import wait_for

    condition = _require_str(arguments, "condition")
    timeout = float(arguments.get("timeout_seconds") or 30.0)
    datamodel = str(arguments.get("datamodel_type") or "Edit")
    studio = _RobloxStudio(
        client=client, studio_id=arguments.get("studio_id")
    )
    verdict = await wait_for(studio, condition, timeout, datamodel_type=datamodel)
    return {
        "content": [{"type": "text", "text": json.dumps(verdict, indent=2)}],
        "isError": False, "is_error": False,
    }


def _manage_instance_actions() -> List[str]:
    """The ``action`` values ``extended_manage_instance`` advertises.

    Read from the schema so the "Accepted:" list in the unknown-action error
    cannot drift from what ``tools/list`` told the caller it may send. The
    parity test checks the two servers advertise the same set; this keeps the
    *error message* honest about the same thing.
    """
    for tool in _EXTENDED_TOOLS:
        if tool.name == "extended_manage_instance":
            return list(
                (tool.input_schema.get("properties") or {})["action"]["enum"]
            )
    return []  # pragma: no cover - the tool is declared above


async def _call_manage_instance(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended import instance as inst

    action = str(arguments.get("action", "list"))
    studio_id: Optional[str] = arguments.get("studio_id")
    # Needed by action=list, which reads the mesh so its rows carry the
    # studio_id that every other tool takes. Unpinned on purpose: this action is
    # a survey, not a targeted call, and pinning it would hide the other
    # Studios it exists to show you.
    studio = _RobloxStudio(client=client, studio_id=None)

    if action == "list":
        processes: List[Dict[str, Any]] = await asyncio.to_thread(
            inst.list_studio_processes
        )
        # The mesh rows come back alongside the process rows rather than joined
        # onto them, because the join is exactly what cannot be trusted here: two
        # Studios on one place both report the name "Place1", so pairing by name
        # is a guess, and this tool has a documented history of reporting a
        # confidently wrong Studio. Handing back both sides lets the caller use
        # the pair when it is unambiguous (the normal case) and see that it is
        # not when it is not - instead of receiving a fabricated match.
        #
        # This is the gap that made `list` useless as a first call: it returned
        # PIDs and no studio_id, so an agent had to follow it with
        # extended_list_studios and correlate the rows itself, guessing at the
        # same pairing.
        try:
            mesh_rows = [
                {
                    "studio_id": str(
                        row.get("id")
                        or row.get("studio_id")
                        or row.get("studioId")
                        or ""
                    ),
                    "name": row.get("name"),
                }
                for row in await studio.list_studios()
            ]
        except Exception as exc:  # noqa: BLE001 - mesh is advisory here
            mesh_rows = []
            mesh_error: Optional[str] = str(exc)
        else:
            mesh_error = None
        result: Dict[str, Any] = {
            "action": action,
            "processes": processes,
            "mesh": mesh_rows,
        }
        if mesh_error is not None:
            result["mesh_error"] = mesh_error
        result["note"] = (
            "One place can be many processes: a -task StartServer Studio spawns "
            "a -task StartClient Studio per player. `processes` are OS processes; "
            "`mesh` rows are what the proxy reports, each carrying the "
            "studio_id every other tool wants. They are NOT joined: pair a "
            "process's place_file with a mesh name yourself, and treat the pair "
            "as unproven when two Studios share one place name."
        )
    elif action == "places":
        result = {"action": action,
                  "candidates": await asyncio.to_thread(inst.list_place_candidates)}
    elif action == "make_place":
        made = await asyncio.to_thread(inst.make_throwaway_place)
        result = {"action": action, "place_path": made,
                  "next": "pass this as place_path to action=launch"}
    elif action == "launch":
        place = arguments.get("place_path")
        if not place:
            raise ToolError(
                "INVALID_ARGUMENT",
                "place_path is required for action='launch'. Run action='places' "
                "or action='make_place' to choose one.",
            )
        result = {"action": action, **await inst.launch_instance(place)}
    elif action == "stop":
        if not studio_id:
            raise ToolError(
                "INVALID_ARGUMENT",
                "studio_id is required for action='stop'. Call action='list' "
                "with this tool - its mesh rows carry one.",
            )
        found = await inst.resolve_pid_for_studio(studio, studio_id, True)
        if not found.get("resolved"):
            result = {"action": action, **found}
        else:
            result = {
                "action": action,
                "studio_id": studio_id,
                "resolved_by": found.get("how"),
                **await asyncio.to_thread(inst.terminate_process, found["pid"]),
            }
    else:
        # The accepted set goes in the message because this is the one error an
        # agent is guaranteed to hit at least once, and without it the caller
        # has to spend a whole extra turn reading the schema. It is read from
        # that schema rather than written out here, so the two cannot drift -
        # the same drift `test_parity.py` exists to catch.
        accepted = sorted(_manage_instance_actions())
        raise ToolError(
            "INVALID_ARGUMENT",
            "unknown action %s. Accepted: %s." % (describe(action), ", ".join(accepted)),
            tool="extended_manage_instance",
            received=action,
            accepted=accepted,
        )

    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_skill(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.skills import call_skill

    name = arguments.get("skill_name")
    if name is not None and not isinstance(name, str):
        raise ToolError(
            "INVALID_ARGUMENT",
            f"skill_name must be a string when given, got {describe(name)}. "
            f"Omit it for the index of available skills.",
        )
    text, _meta = call_skill(name or None)
    return {"content": [{"type": "text", "text": text}], "isError": False, "is_error": False}


async def _call_capture(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.capture import capture_png

    save_path: Optional[str] = arguments.get("save_path")
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    result = await capture_png(studio, save_path=save_path)
    # Echo the target so a caller never re-derives which Studio answered.
    result["studio_id"] = studio_id or studio.studio_id
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_studio_identity(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.registry import read_in_band_identity

    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    identity = await read_in_band_identity(studio)
    result: Dict[str, Any] = {
        "identity": identity,
        "studio_id": studio_id or studio.studio_id,
        "datamodel": "Edit",
        "note": (
            "GetDebugId identifies the DataModel root, not the process. Read it "
            "in Edit mode; a play session reports a different value for the same "
            "Studio. Whether it survives a Studio restart is unverified."
        ),
    }
    if identity is None:
        result["warning"] = (
            "No tagged identity line in the console. The proxy may have "
            "truncated output, or the code may not have run."
        )
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_list_studios(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    from .extended.registry import list_instances, resolve

    refresh = bool(arguments.get("refresh", True))
    selector = arguments.get("resolve")
    if isinstance(selector, dict):
        result: Any = resolve(
            debug_id=selector.get("debug_id"),
            name=selector.get("name"),
            place_id=selector.get("place_id"),
        )
    elif refresh:
        result = await list_instances()
    else:
        from .extended.registry import load_all, registry_path

        data = load_all()
        result = {
            "version": data.get("version"),
            "registry_path": registry_path(),
            "count": len(data.get("instances", {})),
            "instances": list(data.get("instances", {}).values()),
            "note": "refresh=false, so values are as last recorded",
        }
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _call_script_grep(
    client: MCPClient, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    query = _require_str(arguments, "query")
    root_path = arguments.get("root_path")
    # Passed through unconverted. `int("x")` used to raise a bare ValueError
    # that classified as UNKNOWN, and `int("5")` quietly accepted a string the
    # schema declares an integer - Node's `Number("x")` is NaN, which then
    # slipped past the range check entirely. Both languages now hand the raw
    # value to the grep layer, which validates it once and identically.
    context_lines = arguments.get("context_lines", 3)
    regex = bool(arguments.get("regex", False))
    instance_type = arguments.get("instance_type")
    max_results = arguments.get("max_results", 30)
    studio_id: Optional[str] = arguments.get("studio_id")
    studio = _RobloxStudio(client=client, studio_id=studio_id)
    hits = await _grep(
        studio,
        query,
        root_path=root_path,
        context_lines=context_lines,
        regex=regex,
        instance_type=instance_type,
        max_results=max_results,
    )
    return {
        "content": [{"type": "text", "text": json.dumps(hits, indent=2)}],
        "isError": False, "is_error": False,
    }


async def _guard_screen_capture(
    client: MCPClient, arguments: Dict[str, Any]
) -> None:
    """Refuse `screen_capture` when the target is ambiguous. Request P0.1.

    Measured 2026-10-01, three Studios attached: the schema **requires**
    ``studio_id`` and refuses the call without it, so this guard is
    defence-in-depth rather than the primary defence. Over MCP, schema
    validation runs before dispatch and an omitted id never arrives here.

    It still earns its place on the paths that do no validation: a direct
    ``client.call_tool``, and this repo's own ``client.py`` validates no
    schema at all. Whether an older Studio build made the id optional is
    unmeasured.

    Historical reason the guard exists: a measured failure where probes ran
    against one place and every capture came back as an empty baseplate. That
    is no longer explained by the optional-id mechanism this guard was built
    for, so it is recorded as unexplained rather than as a live hazard.

    With exactly one Studio attached, pass through. With more, raise and name
    them. Never pick one.
    """
    if arguments.get("studio_id"):
        return
    studio = _RobloxStudio(client=client, studio_id=None)
    try:
        studios = await studio.list_studios()
    except Exception as exc:  # noqa: BLE001 - do not block on our own failure
        # If the mesh cannot be read, refusing would block a call that may well
        # have been unambiguous. Pass through and say why, rather than turning
        # a transport hiccup into a refusal that reads as a policy decision.
        _RELAY_GUARD_NOTES.append(
            "Could not read the Studio list to check the capture target (%s); "
            "passing through, so the capture may be of the wrong Studio." % exc
        )
        return
    if len(studios) <= 1:
        return
    candidates = [
        {"studio_id": str(s.get("id") or s.get("studio_id") or ""), "name": s.get("name")}
        for s in studios
    ]
    raise ToolError(
        "AMBIGUOUS_STUDIO",
        "screen_capture needs a studio_id, and with %d Studios attached an "
        "omitted one has no single right answer - pass studio_id, or use "
        "extended_capture which reports the Studio it captured."
        % len(studios),
        candidates=candidates,
    )


#: Guards on RELAYED tools: they run before pass-through and may refuse. This is
#: not a rewrite of the tool - the call is forwarded untouched when it passes -
#: but the proxy does hold the arguments, so the "we cannot add parameters to a
#: relayed tool" limit was never a reason to accept a wrong target.
#: Guards on RELAYED tools: they run before pass-through and may refuse. This is
#: not a rewrite of the tool - the call is forwarded untouched when it passes -
#: but the proxy does hold the arguments, so the "we cannot add parameters to a
#: relayed tool" limit was never a reason to accept a wrong target.
#:
#: Typed as ``Callable[..., Awaitable[None]]`` and NOT ``Dict[str, Any]``, on
#: purpose. ``Any`` erases the coroutine type, so a missing ``await`` at the call
#: site type-checks cleanly - which is exactly how the first version shipped the
#: guard without awaiting it, leaving it dead code that the unit tests passed.
_RELAY_GUARDS: Dict[str, Callable[..., Awaitable[None]]] = {
    "screen_capture": _guard_screen_capture,
}

#: Non-fatal notes produced by guards on a pass-through, reported alongside it.
_RELAY_GUARD_NOTES: List[str] = []


_EXTENDED_HANDLERS = {
    "extended_write_script": _call_extended_write,
    "extended_update_script": _call_extended_update,
    "extended_script_grep": _call_script_grep,
    "extended_script_search_and_read": _call_script_search_read,
    "extended_insert_asset_from_file": _call_insert_asset,
    "extended_watch_output": _call_watch_output,
    "extended_run_tests": _call_run_tests,
    "extended_execute_luau_from_file": _call_execute_file,
    "extended_list_studios": _call_list_studios,
    "extended_studio_identity": _call_studio_identity,
    "extended_capture": _call_capture,
    "extended_manage_instance": _call_manage_instance,
    "extended_wait_for": _call_wait_for,
    "extended_skill": _call_skill,
    "extended_breakpoints": _call_breakpoints,
    "extended_clear_breakpoints": _call_clear_breakpoints,
}


#: Tools that only observe. Everything else is left unmarked, which a client reads
#: as mutating, so adding a new tool is safe by default rather than a way to
#: accidentally grant parallel execution.
#:
#: Listed in one place rather than as a flag on each definition because the test
#: that checks it needs to compare this set against the handler set, and a
#: per-tool flag lets those two drift apart silently.
#:
#: Two deliberate omissions:
#:
#: * ``extended_wait_for`` runs a caller-supplied Luau probe, so it can execute
#:   anything the probe executes. It reads in practice and is not marked, because
#:   "in practice" is not a guarantee.
#: * ``extended_manage_instance`` launches and stops Studios. Its ``list`` action
#:   does not, but a single hint cannot describe both.
_READONLY_TOOLS = frozenset({
    "extended_script_grep",
    "extended_script_search_and_read",
    "extended_watch_output",
    "extended_list_studios",
    "extended_studio_identity",
    "extended_capture",
    "extended_skill",
})

for _tool in _EXTENDED_TOOLS:
    _tool.read_only = _tool.name in _READONLY_TOOLS

_unknown = _READONLY_TOOLS - {t.name for t in _EXTENDED_TOOLS}
if _unknown:  # pragma: no cover - guards a rename that was only half done
    raise RuntimeError(
        "read-only set names tools that do not exist: %s" % ", ".join(sorted(_unknown))
    )


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #

#: name -> declared property names, built once from the same objects the
#: ``tools/list`` response is built from, so the two cannot drift.
_TOOL_PROPERTIES: Dict[str, frozenset] = {
    tool.name: frozenset((tool.input_schema.get("properties") or {}))
    for tool in _EXTENDED_TOOLS
}

#: Arguments that are not in any schema, accepted for one reason: a client may
#: send them and dropping them silently is the bug this whole check exists to
#: stop. None currently - listed so adding one is a deliberate act.
_IGNORED_ARGUMENTS: Dict[str, frozenset] = {}


def _reject_unknown_arguments(name: str, arguments: Dict[str, Any]) -> None:
    """Refuse a call carrying a parameter the tool does not declare.

    **This is the single highest-value check in the server**, and it is here
    because of what it prevents rather than what it reports.

    A silently-ignored parameter produces a *plausible wrong answer* rather than
    an error, and three separate incidents in this project were exactly that:

    * ``screen_capture`` accepted ``format: "png"`` and returned JPEG. Eight
      parameter names were tried before that was noticed, because none of them
      errored.
    * Node's ``extended_watch_output`` had no ``pattern``, so passing it returned
      the **whole** console buffer with no error - which reads identically to
      "no breakpoint was hit".
    * ``max_lines: 0`` became 200 through a falsy-``or`` default, so a caller
      asking for no lines got 200.

    In each case the tool reported success. An unknown parameter is almost always
    a typo, a wrong-version schema, or a parameter that exists on the *other*
    implementation - and all three deserve to be loud.

    The message names the closest declared parameter, because the common cause is
    a spelling variant (``replaceAll`` vs ``replace_all``) and a bare "unknown
    key" sends the caller hunting.
    """
    declared = _TOOL_PROPERTIES.get(name)
    if declared is None:
        return
    allowed = declared | _IGNORED_ARGUMENTS.get(name, frozenset())
    if not isinstance(arguments, dict):
        return
    unknown = sorted(set(arguments) - allowed)
    if not unknown:
        return

    lines = []
    for key in unknown:
        suggestion = _closest(key, declared)
        lines.append(
            "%s (did you mean %s?)" % (key, suggestion) if suggestion else key
        )
    raise ToolError(
        "INVALID_ARGUMENT",
        "%s does not accept %s. Accepted: %s"
        % (name, ", ".join(lines), ", ".join(sorted(declared)) or "(none)"),
        tool=name,
        unknown=unknown,
        accepted=sorted(declared),
    )


def _closest(key: str, declared: frozenset) -> Optional[str]:
    """The declared parameter nearest to ``key``, if one is close enough to be
    worth suggesting.

    A deliberately loose bar, and the cutoff was **measured against the real
    vocabulary** rather than guessed. At 0.6 every suggestion was sensible but
    ``line_number`` got nothing (ratio 0.53), and an abbreviation is a common
    caller error. At 0.5 the only change is that one case, and no nonsense
    appears - ``filters``, ``q``, ``xyzzy`` and ``zzzzqqqxyzzy`` still get
    nothing against every tool.

    Below the threshold it returns nothing rather than a bad suggestion, because
    a wrong suggestion is worse than none.
    """
    import difflib

    matches = difflib.get_close_matches(key, sorted(declared), n=1, cutoff=0.5)
    return matches[0] if matches else None


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
        except Exception as exc:  # noqa: BLE001 - classified below
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": _error_payload(exc),
                }
            )
            return
        # Steer callers toward the extended tools: append a note to the relayed
        # tools that have a better equivalent, so the choice happens in the one
        # place it is made. `_STEERS` is mirrored in `parity/tools.json` and both
        # suites assert the two agree, because a drift here is a model using the
        # worse tool forever and nothing anywhere reports it.
        base_tools = base.get("tools") or []
        annotated = []
        for t in base_tools:
            steer = (
                _STEERS.get(str(t.get("name"))) if isinstance(t, dict) else None
            )
            if steer:
                # Appended, never replaced: the relayed description is Studio's
                # and a caller may be relying on it. A caller who wants the raw
                # tool still gets it, with the alternative named.
                t = {
                    **t,
                    "description": f"{t.get('description') or ''} {steer}".strip(),
                }
            annotated.append(t)
        base_tools = annotated
        extended_dicts = [t.to_dict() for t in _EXTENDED_TOOLS]
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
            # Reject unknown parameters *before* dispatch (request P0.2a).
            try:
                _reject_unknown_arguments(name, arguments)
            except ToolError as exc:
                _send({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": _error_payload(exc),
                })
                return
            try:
                result = await _EXTENDED_HANDLERS[name](client, arguments)
            except Exception as exc:  # noqa: BLE001 - classified below
                _send({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": _error_payload(exc),
                })
                return
            _send(
                {"jsonrpc": "2.0", "id": message["id"], "result": result}
            )
            return

        # A guard on a RELAYED call, not a rewritten one. When `screen_capture`
        # arrives without a studio_id and several Studios are attached, there is
        # no single right answer to forward it to. This project has treated that
        # as unfixable because the tool belongs to Studio, which is only true of
        # the tool's *implementation*. The request arrives here first, so the
        # proxy can see that the caller disambiguated nothing.
        #
        # Measured 2026-10-01: the schema requires studio_id, so over MCP an
        # omitted id never reaches here - validation runs first. The guard is
        # defence-in-depth for callers that validate nothing.
        #
        # So it refuses rather than guessing, which is the same rule every other
        # ambiguous path in this project follows: name the candidates and refuse,
        # rather than pick. Passing the call through is the one option that
        # cannot be defended, because it produces a plausible wrong answer.
        guard = _RELAY_GUARDS.get(str(name))
        if guard is not None:
            try:
                # AWAITED. This line shipped without it, and that made the whole
                # P0.1 guard dead code: the coroutine was created and dropped, so
                # `except ToolError` below could never fire, `_RELAY_GUARD_NOTES`
                # never populated, and `screen_capture` captured the wrong Studio
                # exactly as before - on the Python server, which is the one
                # actually configured. The unit tests passed because they called
                # `_guard_screen_capture` directly and never went through dispatch,
                # and pyright missed it because `_RELAY_GUARDS` is typed
                # `Dict[str, Any]`, which erases the coroutine type. A test that
                # skips the call path is a test that cannot see this class of bug.
                await guard(client, arguments)
            except ToolError as exc:
                _send({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": _error_payload(exc),
                })
                return

        # Pass through to the underlying Studio MCP.
        _RELAY_GUARD_NOTES.clear()
        try:
            result = await client.request("tools/call", params)
        except Exception as exc:  # noqa: BLE001 - classified below
            _send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": _error_payload(exc),
            })
            return
        _send(
            {"jsonrpc": "2.0", "id": message["id"], "result": result}
        )
        # A guard that could not run its check passes the call through, but says so
        # rather than staying silent - "we verified" and "we could not check"
        # must not look the same to a caller.
        if _RELAY_GUARD_NOTES:
            result = dict(result or {})
            result["proxy_note"] = list(_RELAY_GUARD_NOTES)
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
