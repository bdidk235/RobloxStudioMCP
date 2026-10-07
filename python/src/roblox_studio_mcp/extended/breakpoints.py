"""Breakpoints via ``ScriptDebuggerService``.

Proven against a live Studio: ``AddBreakpoint`` registers and verifies, a
non-continuing breakpoint genuinely halts the running server, and per-hit data
can be captured with live locals.

The one non-obvious part: how hits are reported
----------------------------------------------
``ScriptDebuggerService`` takes a ``LogMessage`` which is a **Luau expression
evaluated on every hit**, but a *successful* log injection is not visible through
``get_console_output`` — it goes to Studio's own log sink. An expression that
**errors** is surfaced instead, one line per hit, already prefixed by the
engine:

    Breakpoint ServerScriptService.BpTest:5 ignored: [string "logExpression"]:1: HIT i=7

So the default log expression is ``error(...)``: it is a deliberate failure, and
that failure is the reporting channel. Measured on a loop of 80 iterations:
**60 contiguous hits, i=1..60, from the first iteration**, with the live local
interpolated into each line.

Three things that are not optional, each of them learned the hard way:

* **Always ``ContinueExecution = true``.** A non-continuing breakpoint halts the
  whole script scheduler, which blocks the very tool call that set it — measured
  as a 120 s MCP timeout with the probe never resuming. Pausing works; it just
  cannot be driven from a synchronous tool call.
* **The registry folder must be stable across calls.** Each ``execute_luau`` is
  a fresh invocation, so a per-call registry folder reads as empty to the next
  one. That produced a ``list`` that always returned nothing while the
  breakpoint itself worked perfectly.
* **Read hits by pattern, not by line.** The console's newline shape is
  consumer-dependent - through some callers it arrives as ONE line with
  literal ``\n`` escapes, through the agent caller path with real newlines -
  so ``splitlines()`` is right on one shape and silently wrong on the other.
  Match the expression's own text (or the ``Breakpoint `` hit prefix) instead
  of parsing lines; see ``rsx-breakpoints`` for the digit-scan failure that
  made 60 correct hits look like garbage.

``Plugin:GetSetting``-backed breakpoint persistence, as used by Chrrxs, is
plugin-only and does not port. Everything here is a plain service call.

Requires a play session: ``AddBreakpoint`` targets ``ServerScriptService``, so
this runs against the Server data model.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

from ..roblox import RobloxStudio
from ..types import CallToolResult
from .errors import INVALID_ARGUMENT, ToolError, describe

#: Engine-side prefix, so a caller can filter hits out of a shared console.
HIT_PREFIX = "Breakpoint "

#: Luau for every registry operation. ``ScriptDebuggerService`` exposes no way
#: to enumerate breakpoints, so the registry is mirrored into a studio-only
#: folder under ``PluginGuiService`` - which never replicates or publishes.
_REGISTER_LUAU = """
local PG = game:GetService("PluginGuiService")
local DBG = game:GetService("ScriptDebuggerService")
local ROOT = "RBXBreakpoints"

local holder = PG:FindFirstChild(ROOT)
if not holder then
    holder = Instance.new("Folder")
    holder.Name = ROOT
    holder.Parent = PG
end

-- A STABLE registry folder. Each execute_luau call is a fresh invocation, so
-- a per-call folder reads as empty to the next one - which is exactly why
-- `list` returned nothing while the breakpoint itself worked fine.
local db = holder:FindFirstChild("entries")
if not db then
    db = Instance.new("Folder")
    db.Name = "entries"
    db.Parent = holder
end

local function key(path, line)
    -- Instance names are safest without punctuation, and ":" is the separator
    -- script_read uses for paths, so it is avoided in a stored name.
    return (tostring(path):gsub("[^%w_]", "_")) .. "_L" .. tostring(line)
end

local function register(path, line, expr)
    local script = game:GetService("ServerScriptService"):FindFirstChild(path)
    if not script then return "no-such-script:" .. path end
    -- ContinueExecution must be true: a pause blocks the calling tool call.
    local ok, res = pcall(function()
        return DBG:AddBreakpoint(script, {
            Line = line,
            Enabled = true,
            ContinueExecution = true,
            LogMessage = expr,
        })
    end)
    if not ok then return "error:" .. tostring(res) end
    if not res.Verified then return "unverified:" .. tostring(res.Message) end
    local rec = db:FindFirstChild(key(path, line))
    if not rec then
        rec = Instance.new("StringValue")
        rec.Name = key(path, line)
        rec.Parent = db
    end
    rec.Value = tostring(path) .. ":" .. tostring(line) .. " = " .. tostring(expr)
    return "ok"
end

local op = %(OP)s
local path = %(PATH)s
local line = %(LINE)s
local expr = %(EXPR)s

if op == "set" then
    return register(path, line, expr)
end
if op == "remove" then
    local script = game:GetService("ServerScriptService"):FindFirstChild(path)
    if not script then return "no-such-script:" .. path end
    local ok = pcall(function() DBG:RemoveBreakpoint(script, line) end)
    local rec = db:FindFirstChild(key(path, line))
    if rec then rec:Destroy() end
    return ok and "ok" or "error"
end
if op == "clear" then
    pcall(function() DBG:ClearBreakpoints() end)
    db:ClearAllChildren()
    return "ok"
end
if op == "list" then
    local out = {}
    for _, rec in db:GetChildren() do out[#out + 1] = rec.Name .. "|" .. rec.Value end
    table.sort(out)
    return "count=" .. #out .. " " .. table.concat(out, " | ")
end
return "unknown-op"
"""


#: The four registry operations. A ``Literal`` rather than ``str`` because the
#: Luau dispatches on the exact string and falls through to ``"unknown-op"`` for
#: anything else — a typo would then read as a successful no-op call rather than
#: an error, because the Python side only checks the *result*.
_RegistryOp = Literal["set", "remove", "clear", "list"]


def _json(value: str) -> str:
    return json.dumps(value)


async def _register(studio: RobloxStudio, op: _RegistryOp, path: Optional[str],
                    line: Optional[int], expr: Optional[str]) -> str:
    code = (
        _REGISTER_LUAU
        .replace("%(OP)s", _json(op))
        .replace("%(PATH)s", _json(path or ""))
        .replace("%(LINE)s", str(int(line or 0)))
        .replace("%(EXPR)s", _json(expr or ""))
    )
    result: CallToolResult = await studio.call(
        "execute_luau", {"code": code, "datamodel_type": "Server"}
    )
    return result.text()


class Breakpoint(TypedDict):
    """One registered breakpoint, as :func:`list_breakpoints` reports it.

    ``line`` is ``Union[int, str]`` and that is deliberate rather than lazy: the
    registry value is the flat string ``path:line = expr``, and when the tail
    after the last colon is not a number the raw text is returned instead of
    being coerced. A record can only reach that state if it was written by
    something other than this module. Coercing it to ``0`` here would have been
    tidier and wrong — it would report a breakpoint on line 0 rather than
    admitting the record is unparseable — so the type records what happens.
    """

    script_path: str
    line: Union[int, str]
    log_expression: str


class SetBreakpointResult(TypedDict):
    """The six keys :func:`set_breakpoint` puts in its return value.

    Declared, and asserted by ``tests/test_breakpoints.py``, but the function is
    still annotated ``Dict[str, Any]``: its only caller assigns the result into a
    ``Dict[str, Any]`` in ``extended_server.py`` (line 968), and pyright rejects
    a TypedDict there. Widening the annotation is a one-line change to that file
    once it is not in flight; the shape is pinned here so it cannot drift in the
    meantime.

    ``verified`` and ``continue_execution`` are always ``True`` in practice and
    are kept as keys rather than dropped, because the caller serialises this
    straight to the model and the two facts are the whole safety argument for the
    tool: a verified breakpoint, and one that does not halt the scheduler.
    """

    script_path: str
    line: int
    log_expression: str
    verified: bool
    continue_execution: bool
    hit_prefix: str


async def list_breakpoints(studio: RobloxStudio) -> List[Breakpoint]:
    """Return the breakpoints registered through this client."""
    raw = await _register(studio, "list", None, None, None)
    _, _, rest = raw.partition("count=")
    if not rest:
        return []
    # rest begins with the count and a space, then "path:line = expr" entries.
    rest = rest.partition(" ")[2]
    out: List[Breakpoint] = []
    for item in rest.split(" | "):
        if not item.strip():
            continue
        _, _, value = item.partition("|")
        location, _, expr = value.partition(" = ")
        path, _, lineno = location.rpartition(":")
        out.append(
            {
                "script_path": path,
                "line": int(lineno) if lineno.isdigit() else lineno,
                "log_expression": expr,
            }
        )
    return out


async def set_breakpoint(
    studio: RobloxStudio,
    script_path: str,
    line: int,
    *,
    log_expression: Optional[str] = None,
) -> Dict[str, Any]:
    """Set a non-halting breakpoint on ``script_path`` at 1-based ``line``.

    ``log_expression`` is a Luau expression evaluated on every hit. It should
    *fail* — the default is ``error(...)`` — because a successful log injection
    is not visible through ``get_console_output``. Locals in scope at that line
    can be interpolated, e.g. ``error("i=" .. tostring(i))``.
    """
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        # The type check exists for the caller's own Lua errors:
        # rejected `2.5` and `"3"`, this one used to let both through and send
        # them to Studio. Same input, different behaviour per server.
        raise ToolError(
            INVALID_ARGUMENT,
            f"line must be a 1-based positive integer, got {describe(line)}.",
        )
    if not log_expression:
        log_expression = 'error("hit")'
    status = await _register(studio, "set", script_path, line, log_expression)
    if not status.startswith("ok"):
        raise RuntimeError(f"could not set breakpoint: {status}")
    return {
        "script_path": script_path,
        "line": line,
        "log_expression": log_expression,
        "verified": True,
        "continue_execution": True,
        "hit_prefix": HIT_PREFIX,
    }


async def remove_breakpoint(studio: RobloxStudio, script_path: str, line: int) -> bool:
    status = await _register(studio, "remove", script_path, line, None)
    if not status.startswith("ok"):
        raise RuntimeError(f"could not remove breakpoint: {status}")
    return True


async def clear_breakpoints(studio: RobloxStudio) -> bool:
    status = await _register(studio, "clear", None, None, None)
    if not status.startswith("ok"):
        raise RuntimeError(f"could not clear breakpoints: {status}")
    return True
