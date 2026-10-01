/**
 * Breakpoints via `ScriptDebuggerService`.
 *
 * Proven against a live Studio: 60 contiguous hits on a loop of 80, i=1..60,
 * from the first iteration, with the live local interpolated into each line.
 *
 * The one non-obvious part: how hits are reported
 * ----------------------------------------------
 * `ScriptDebuggerService` takes a `LogMessage` which is a **Luau expression
 * evaluated on every hit**, but a *successful* log injection is not visible
 * through `get_console_output` - it goes to Studio's own log sink. An expression
 * that **errors** is surfaced instead, one line per hit, already prefixed by the
 * engine:
 *
 *     Breakpoint ServerScriptService.BpTest:5 ignored: [string "logExpression"]:1: HIT i=7
 *
 * So the default log expression is `error(...)`: a deliberate failure, and that
 * failure is the reporting channel.
 *
 * Three things that are not optional, each learned the hard way:
 *
 * - **Always `ContinueExecution = true`.** A non-continuing breakpoint halts the
 *   whole script scheduler, which blocks the very tool call that set it -
 *   measured as a 120 s MCP timeout with the probe never resuming. Pausing
 *   works; it just cannot be driven from a synchronous tool call.
 * - **The registry folder must be stable across calls.** Each `execute_luau` is a
 *   fresh invocation, so a per-call registry folder reads as empty to the next
 *   one. That produced a `list` that always returned nothing while the
 *   breakpoint itself worked perfectly.
 * - **Read hits by pattern, not by line.** The console arrives as ONE line with
 *   literal `\n` escapes, so `split("\n")` finds a single line and a digit scan
 *   swallows the interleaved output around each hit. Match the expression's own
 *   text instead.
 *
 * `Plugin:GetSetting`-backed breakpoint persistence, as used by Chrrxs, is
 * plugin-only and does not port. Everything here is a plain service call.
 *
 * Requires a play session: `AddBreakpoint` targets `ServerScriptService`, so this
 * runs against the Server data model.
 */

import type { RobloxStudio } from "../roblox.js";
import { INVALID_ARGUMENT, ToolError, describe } from "./errors.js";

/** Engine-side prefix, so a caller can filter hits out of a shared console. */
export const HIT_PREFIX = "Breakpoint ";

/**
 * Luau for every registry operation. `ScriptDebuggerService` exposes no way to
 * enumerate breakpoints, so the registry is mirrored into a studio-only folder
 * under `PluginGuiService` - which never replicates or publishes.
 */
export const REGISTER_LUAU = `
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
-- \`list\` returned nothing while the breakpoint itself worked fine.
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

local op = __OP__
local path = __PATH__
local line = __LINE__
local expr = __EXPR__

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
`;

/** JSON-encode a value for safe inlining into the Luau above. */
function luauLiteral(value: string): string {
  return JSON.stringify(value);
}

async function register(
  studio: RobloxStudio,
  op: string,
  path: string | null,
  line: number | null,
  expr: string | null,
): Promise<string> {
  const code = REGISTER_LUAU.replace("__OP__", luauLiteral(op))
    .replace("__PATH__", luauLiteral(path ?? ""))
    .replace("__LINE__", String(Math.trunc(line ?? 0)))
    .replace("__EXPR__", luauLiteral(expr ?? ""));
  const result = await studio.call("execute_luau", { code, datamodel_type: "Server" });
  return result.text();
}

export interface Breakpoint {
  script_path: string;
  line: number | string;
  log_expression: string;
}

export interface SetBreakpointResult extends Breakpoint {
  verified: true;
  continue_execution: true;
  hit_prefix: string;
}

/** Return the breakpoints registered through this client. */
export async function listBreakpoints(studio: RobloxStudio): Promise<Breakpoint[]> {
  const raw = await register(studio, "list", null, null, null);
  const at = raw.indexOf("count=");
  if (at < 0) return [];
  // rest begins with the count and a space, then "path:line = expr" entries.
  let rest = raw.slice(at + "count=".length);
  const space = rest.indexOf(" ");
  if (space < 0) return [];
  rest = rest.slice(space + 1);

  const out: Breakpoint[] = [];
  for (const item of rest.split(" | ")) {
    if (!item.trim()) continue;
    const value = item.slice(item.indexOf("|") + 1);
    const eq = value.indexOf(" = ");
    const location = eq < 0 ? value : value.slice(0, eq);
    const expr = eq < 0 ? "" : value.slice(eq + 3);
    const colon = location.lastIndexOf(":");
    const path = colon < 0 ? location : location.slice(0, colon);
    const lineno = colon < 0 ? "" : location.slice(colon + 1);
    out.push({
      script_path: path,
      line: /^\d+$/.test(lineno) ? Number(lineno) : lineno,
      log_expression: expr,
    });
  }
  return out;
}

/**
 * Set a non-halting breakpoint on `scriptPath` at 1-based `line`.
 *
 * `logExpression` is a Luau expression evaluated on every hit. It should *fail* -
 * the default is `error(...)` - because a successful log injection is not visible
 * through `get_console_output`. Locals in scope at that line can be
 * interpolated, e.g. `error("i=" .. tostring(i))`.
 */
export async function setBreakpoint(
  studio: RobloxStudio,
  scriptPath: string,
  line: number,
  options: { logExpression?: string } = {},
): Promise<SetBreakpointResult> {
  if (!Number.isInteger(line) || line < 1) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "line must be a 1-based positive integer, got " + describe(line) + ".",
    );
  }
  const logExpression = options.logExpression || 'error("hit")';
  const status = await register(studio, "set", scriptPath, line, logExpression);
  if (!status.startsWith("ok")) {
    throw new Error(`could not set breakpoint: ${status}`);
  }
  return {
    script_path: scriptPath,
    line,
    log_expression: logExpression,
    verified: true,
    continue_execution: true,
    hit_prefix: HIT_PREFIX,
  };
}

export async function removeBreakpoint(
  studio: RobloxStudio,
  scriptPath: string,
  line: number,
): Promise<true> {
  const status = await register(studio, "remove", scriptPath, line, null);
  if (!status.startsWith("ok")) {
    throw new Error(`could not remove breakpoint: ${status}`);
  }
  return true;
}

export async function clearBreakpoints(studio: RobloxStudio): Promise<true> {
  const status = await register(studio, "clear", null, null, null);
  if (!status.startsWith("ok")) {
    throw new Error(`could not clear breakpoints: ${status}`);
  }
  return true;
}
