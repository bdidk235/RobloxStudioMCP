/**
 * Write-like wrapper around `multi_edit` for game-tree scripts.
 *
 * Mirrors Claude Code Write semantics for game-tree scripts. The Studio
 * MCP's `multi_edit` is great for targeted string replacements, but
 * sometimes you just want to replace a script's *entire* body — the
 * `open(path, "w")` equivalent for game scripts. This module provides
 * {@link writeScript}:
 *
 * - Read first: the current source is always fetched via `script_read`
 *   before writing (like Write requires Read before overwriting).
 * - Atomic: the whole body is replaced in a single `multi_edit` call.
 * - Idempotent: re-running with identical content is a safe no-op
 *   (`"unchanged"` — nothing is written).
 * - Never create implicitly: a missing script is only created when
 *   `createIfMissing` is explicitly set (like Write never creates new files
 *   unless asked). Otherwise the missing-script error propagates.
 * - Game-tree only: `targetPath` must start with `"game."` (a DataModel
 *   dot-path). File-system paths are not accepted — use a plain file write
 *   for those.
 * - Chunked: for content over the 200K `Script.Source` limit, uses an
 *   append-slice pattern (like `ScriptEditorService:UpdateSourceAsync`)
 *   to build the source up incrementally.
 */

import type { RobloxStudio } from "../roblox.js";
import { INVALID_ARGUMENT, ToolError, describe } from "./errors.js";

export const GAME_TREE_PREFIX = "game.";

// Roblox engine hard limit on Script.Source (and any string property).
// Beyond this, direct assignment fails; use the append-slice pattern instead.
export const STRING_PROPERTY_SIZE_LIMIT = 200_000;

export const VALID_SCRIPT_CLASSES: ReadonlySet<string> = new Set([
  "Script",
  "LocalScript",
  "ModuleScript",
]);

// `script_read` prefixes each line as `<line-number>→<content>` (e.g. `12→print(1)`).
// Only strip that leading pattern so a literal `→` inside the source survives.
const LINE_PREFIX_RE = /^\s*\d+\s*→/;

// Heuristic substrings indicating script_read failed because the script is
// missing (vs. a connection/permission failure that must not be masked).
//
// Bare `nil` and `unknown` were here and are removed. They are ordinary words
// in a Luau fault - "attempt to index nil (field 'X')" is a caller's own bug -
// so under `create_if_missing` they made this report `created` for a script
// that was never missing, silently. Measured on the Python side: 5 of 7
// unrelated errors matched. `is missing` keeps the intent without the overlap;
// `python/tests/test_missing_heuristic.py` pins the negative list.
const MISSING_HINTS = [
  "not found",
  "not exist",
  "no such",
  "could not find",
  "couldn't find",
  "does not exist",
  "doesn't exist",
  "is missing",
];

export const MAX_CHUNKED_RETRIES = 5;

export type WriteStatus = "wrote" | "unchanged" | "created";

export interface WriteOptions {
  className?: string | null;
  createIfMissing?: boolean;
  returnString?: boolean;
}

/** Remove `LINE_NUMBER→` prefixes returned by `script_read`. */
export function stripLinePrefixes(text: string): string {
  return text
    .split("\n")
    .map((line) => line.replace(LINE_PREFIX_RE, ""))
    .join("\n");
}

export function validateClassName(className: string): string {
  if (!VALID_SCRIPT_CLASSES.has(className)) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "className must be one of " + JSON.stringify([...VALID_SCRIPT_CLASSES].sort()) +
        "; got " + describe(className) + ".",
    );
  }
  return className;
}

/**
 * Pick a `[===[...]===]` level whose closer appears in none of `parts`.
 *
 * Long-bracket strings are raw — no escapes needed — so we just bump the
 * `=` count until `]===]` can't collide with the payload.
 */
export function pickBracketLevel(...parts: string[]): number;
export function pickBracketLevel(startOrPart: number | string, ...rest: string[]): number;
export function pickBracketLevel(first: number | string = 10, ...rest: string[]): number {
  let start = 10;
  let parts: string[];
  if (typeof first === "number") {
    start = first;
    parts = rest;
  } else {
    parts = [first, ...rest];
  }
  let level = start;
  for (;;) {
    const closer = `]${"=".repeat(level)}]`;
    if (parts.some((p) => p.includes(closer))) {
      level += 1;
      continue;
    }
    return level;
  }
}

/**
 * Wrap `value` as a raw Lua long-bracket string (no escapes needed).
 *
 * A leading newline is guarded: Lua skips the first character of a
 * long-bracket string when it is a newline, so values starting with
 * `"\n"`/`"\r"` get one extra newline that Lua consumes, preserving the
 * payload exactly. This matters for chunked slices, which may start
 * anywhere — including right after a newline.
 */
export function luaLongBracket(value: string, level: number): string {
  const eq = "=".repeat(level);
  const guard = value.startsWith("\n") || value.startsWith("\r") ? "\n" : "";
  return `[${eq}[${guard}${value}]${eq}]`;
}

export function isMissingError(exc: unknown): boolean {
  const msg = String(exc instanceof Error ? exc.message : exc).toLowerCase();
  return MISSING_HINTS.some((hint) => msg.includes(hint));
}

export function splitTarget(targetPath: string): [container: string, name: string] {
  const parts = targetPath.split(".");
  if (parts.length < 2) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "target_path must be a DataModel dot-path like " +
        "'game.ServerScriptService.MyScript'; got " + describe(targetPath) + ".",
    );
  }
  return [parts.slice(0, -1).join("."), parts[parts.length - 1]];
}

/**
 * Build the Lua script for chunked writing with the given bracket level.
 *
 * Slices are embedded as raw long-bracket strings — no escapes needed.
 * `name` uses the same raw form so quotes/backslashes can't break it.
 */
export function buildChunkedLua(
  container: string,
  name: string,
  className: string,
  slices: string[],
  level: number,
): string {
  const sliceEntries = slices.map((s) => luaLongBracket(s, level));
  const slicesTable = `{${sliceEntries.join(",")}}`;
  const nameLit = luaLongBracket(name, level);
  return (
    `local slices = ${slicesTable}\n` +
    `local ss = game:GetService("ScriptEditorService")\n` +
    `local target = ${container}:FindFirstChild(${nameLit})\n` +
    `if not target then\n` +
    `    target = Instance.new("${className}")\n` +
    `    target.Name = ${nameLit}\n` +
    `    target.Parent = ${container}\n` +
    `    target.Source = ""\n` +
    `end\n` +
    `for i = 1, #slices do\n` +
    `    local slice = slices[i]\n` +
    // Capture the index: UpdateSourceAsync callbacks may run after the
    // loop finishes, when shared `i` would read as #slices + 1 for all
    // of them (appending instead of replacing on slice 1).
    `    local idx = i\n` +
    `    ss:UpdateSourceAsync(target, function(old)\n` +
    `        if idx == 1 then\n` +
    `            return slice\n` +
    `        else\n` +
    `            return old .. slice\n` +
    `        end\n` +
    `    end)\n` +
    `end\n`
  );
}

/**
 * Write content >200K via a single execute_luau with a slice loop.
 *
 * Uses `ScriptEditorService:UpdateSourceAsync` inside Studio in a loop,
 * passing each 200K slice individually. Studio handles the accumulation
 * internally and bypasses the 200K Source assignment limit.
 */
export async function chunkedWrite(
  studio: RobloxStudio,
  targetPath: string,
  content: string,
  options: { className?: string } = {},
): Promise<void> {
  const { className = "Script" } = options;
  const slices: string[] = [];
  for (let i = 0; i < content.length; i += STRING_PROPERTY_SIZE_LIMIT) {
    slices.push(content.slice(i, i + STRING_PROPERTY_SIZE_LIMIT));
  }

  if (slices.length === 0) {
    return;
  }

  validateClassName(className);
  const [container, name] = splitTarget(targetPath);

  // Pick a collision-free level upfront (raw long-brackets need no escapes);
  // keep the parse-retry as a safety net for exotic Studio parsing quirks.
  let level = pickBracketLevel(name, ...slices);
  for (let attempt = 0; attempt <= MAX_CHUNKED_RETRIES; attempt++) {
    const luaScript = buildChunkedLua(container, name, className, slices, level);
    try {
      await studio.executeLuau(luaScript, "Edit");
      return;
    } catch (exc) {
      if (String(exc instanceof Error ? exc.message : exc).toLowerCase().includes("parse") && attempt < MAX_CHUNKED_RETRIES) {
        level += 1;
        continue;
      }
      throw exc;
    }
  }
  // Deliberately left a bare `Error`, and so deliberately left classifying as
  // UNKNOWN: this is our own encoder failing to escape the caller's content,
  // not a bad argument and not the engine refusing. UNKNOWN is the honest code
  // for "we do not know what went wrong", and pinning it to INVALID_ARGUMENT
  // would tell the caller to edit the request when the request was fine.
  throw new Error("Chunked write failed: Lua long-bracket collision persists.");
}

/**
 * Atomically replace a script's contents with Claude Code Write semantics.
 *
 * Reads the current source first; returns `"unchanged"` without writing when
 * the content is already identical. Creates the script only when
 * `createIfMissing` is true.
 *
 * Returns a status: `"wrote"`, `"unchanged"`, or `"created"`.
 */
export async function writeScript(
  studio: RobloxStudio,
  targetPath: string,
  content: string,
  options: WriteOptions = {},
): Promise<WriteStatus> {
  let { className = null, createIfMissing = false, returnString = false } = options;
  let body = content;

  if (returnString) {
    className = "ModuleScript";
    const level = pickBracketLevel(body);
    body = `return ${luaLongBracket(body, level)}`;
  }

  if (!targetPath.startsWith(GAME_TREE_PREFIX)) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "target_path must start with " + describe(GAME_TREE_PREFIX) +
        " (game-tree path); got " + describe(targetPath) +
        ". Use a DataModel dot-path like 'game.ServerScriptService.MyScript'.",
    );
  }

  const resolvedClass = validateClassName(className ?? "Script");

  let currentSource: string;
  try {
    const result = await studio.scriptRead(targetPath);
    currentSource = stripLinePrefixes(result.text());
  } catch (exc) {
    if (!createIfMissing) {
      throw exc;
    }
    // Don't mask connection/permission failures as "missing".
    if (!isMissingError(exc)) {
      throw exc;
    }
    const [container, name] = splitTarget(targetPath);

    if (body.length > STRING_PROPERTY_SIZE_LIMIT) {
      // For large content, create an EMPTY script first, then chunked-write.
      // Can't assign Source directly in execute_luau due to 200K limit.
      const level = pickBracketLevel(name);
      const nameLit = luaLongBracket(name, level);
      await studio.executeLuau(
        `local parent = ${container}\n` +
          `local newScript = Instance.new("${resolvedClass}")\n` +
          `newScript.Name = ${nameLit}\n` +
          `newScript.Source = ''\n` +
          `newScript.Parent = parent\n`,
        "Edit",
      );
      await chunkedWrite(studio, targetPath, body, { className: resolvedClass });
      return "created";
    }

    // Small create: raw long-brackets for both Name and Source.
    const level = pickBracketLevel(name, body);
    const nameLit = luaLongBracket(name, level);
    const sourceLit = luaLongBracket(body, level);
    await studio.executeLuau(
      `local parent = ${container}\n` +
        `local newScript = Instance.new("${resolvedClass}")\n` +
        `newScript.Name = ${nameLit}\n` +
        `newScript.Source = ${sourceLit}\n` +
        `newScript.Parent = parent\n`,
      "Edit",
    );
    return "created";
  }

  if (currentSource === body) {
    return "unchanged";
  }

  if (body.length > STRING_PROPERTY_SIZE_LIMIT) {
    await chunkedWrite(studio, targetPath, body, { className: resolvedClass });
    return "wrote";
  }

  await studio.call("multi_edit", {
    file_path: targetPath,
    datamodel_type: "Edit",
    edits: [{ old_string: currentSource, new_string: body }],
  });
  return "wrote";
}
