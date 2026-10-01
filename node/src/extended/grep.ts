import type { RobloxStudio } from "../roblox.js";
import { extractBalanced } from "../types.js";
import { INVALID_ARGUMENT, ToolError, describe } from "./errors.js";
import { stripLinePrefixes } from "./writer.js";

export interface GrepHit {
  path: string;
  line_number: number;
  excerpt: string;
  context_lines: number;
}

export interface GrepOptions {
  rootPath?: string;
  contextLines?: number;
  regex?: boolean;
  instanceType?: string;
  maxResults?: number;
}

/**
 * One hit line from `script_grep`'s **text** reply, captured from a live call:
 *
 *     Path: game.ServerScriptService.UsabilityProbe | Line: 2 | print('PROBE')
 *
 * Anchored, and the path is non-greedy so a `|` inside the *content* cannot
 * swallow the `Line:` field. Measured content in this format does contain pipes.
 */
const GREP_TEXT_RE =
  /^Path:\s*(.+?)\s*\|\s*Line:\s*(\d+)\s*\|\s?(.*)$/;

/**
 * Parse the plain-text reply format `script_grep` actually returns.
 *
 * **This was missing, and it made the tool return an empty array with a success
 * status for text that provably existed** - the worst failure shape in this
 * project, because an agent searching for something it had just written was told
 * it was not there.
 *
 * The trailing `... Search stopped after reaching the limit of 50 matches.` is a
 * **truncation notice, not a hit**, and does not match, so it is dropped. Reading
 * it as a hit would report a file named after an English sentence.
 */
function parseGrepText(text: string): Array<Record<string, unknown>> {
  const hits: Array<Record<string, unknown>> = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (!line.trim()) continue;
    const match = GREP_TEXT_RE.exec(line);
    if (!match) continue;
    hits.push({
      path: match[1],
      line_number: Number(match[2]),
      content: match[3],
    });
  }
  return hits;
}

/**
 * Coerce a JSON array into hit dicts, dropping anything that is not one.
 *
 * Added because the fallback in `extendedScriptGrep` assigned a raw `string[]`
 * from `rawResult.json()` and the next line called `hit.get("path")` on a string -
 * an unhandled `TypeError` escaping the tool, with no error code and no recovery.
 */
function asHitDicts(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is Record<string, unknown> =>
      typeof item === "object" && item !== null && !Array.isArray(item),
  );
}

function parseGrepRaw(text: string): unknown[] {
  const trimmed = text.trim();
  const raw = extractBalanced(trimmed);
  if (raw !== null) {
    try {
      const data = JSON.parse(raw);
      if (Array.isArray(data)) return data;
      if (data && Array.isArray((data as Record<string, unknown>).results)) {
        return (data as Record<string, unknown>).results as unknown[];
      }
    } catch {}
  }
  try {
    const data = JSON.parse(text);
    if (Array.isArray(data)) return data;
    if (data && Array.isArray((data as Record<string, unknown>).results))
      return (data as Record<string, unknown>).results as unknown[];
  } catch {}
  // Text format, last: the JSON attempts are cheap and unambiguous, and trying
  // them first means a JSON reply is never mis-read as prose.
  return parseGrepText(text);
}

function excerpt(source: string, lineNo: number, context: number): string {
  const lines = source.split("\n");
  const i = Math.max(0, lineNo - 1 - context);
  const j = Math.min(lines.length, lineNo + context);
  return lines.slice(i, j).join("\n");
}

/**
 * An integer option, checked for both type and range.
 *
 * The type check is not pedantry. The call site used `Number(...)`, so a
 * non-numeric string became `NaN`; every comparison against `NaN` is false, so
 * `NaN < 1` and `NaN > 100` both passed and the cap was applied as
 * `hits.slice(0, NaN)`. Python rejected the same input. Mirrors
 * `_bounded_int` in `python/src/roblox_studio_mcp/extended/grep.py`.
 */
function boundedInt(name: string, value: unknown, low: number, high: number): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new ToolError(
      INVALID_ARGUMENT,
      name + " must be an integer, got " + describe(value) + ".",
    );
  }
  if (value < low || value > high) {
    throw new ToolError(
      INVALID_ARGUMENT,
      name + " must be between " + low + " and " + high + ", got " + value + ".",
    );
  }
  return value;
}


export async function extendedScriptGrep(
  studio: RobloxStudio,
  query: string,
  options: GrepOptions = {},
): Promise<GrepHit[]> {
  // Every fault here is the caller's own argument, so every one carries
  // INVALID_ARGUMENT explicitly rather than relying on `classify()` to
  // recognise prose. The two range checks used to be bare `Error`s that no
  // pattern matched, so an agent branching on the code was told "unknown"
  // about a fault that was unambiguously its own.
  if (typeof query !== "string" || query.trim().length === 0) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "query must be a non-empty string, got " + describe(query) + ".",
    );
  }
  const maxResults = boundedInt("max_results", options.maxResults ?? 30, 1, 100);
  const contextLines = boundedInt("context_lines", options.contextLines ?? 3, 0, 10);
  const regex = options.regex ?? false;
  if (regex) {
    // Compiled up front rather than mid-loop. The old site was inside the
    // per-hit branch, so an invalid regex was only reported when a hit
    // happened to lack an excerpt - the same input either errored or silently
    // worked depending on Studio's output.
    try {
      new RegExp(query);
    } catch (e) {
      throw new ToolError(
        INVALID_ARGUMENT,
        "query is not a valid regex (regex=true): " + (e instanceof Error ? e.message : String(e)),
      );
    }
  }

  const rawKwargs: Record<string, unknown> = { query };
  if (options.rootPath) rawKwargs["root_path"] = options.rootPath;
  if (options.instanceType) rawKwargs["instance_type"] = options.instanceType;

  const raw = await studio.call("script_grep", rawKwargs);
  // Coerced, not cast. A `cast` is a promise the compiler cannot check, and the
  // value really can be a `string[]` - Python raised `AttributeError` on exactly
  // that input, and JavaScript would have dropped every real hit silently
  // instead, because `("abc")["path"]` is `undefined` rather than a throw.
  const hits = asHitDicts(parseGrepRaw(raw.text()));
  const enriched: GrepHit[] = [];
  for (const hit of hits.slice(0, maxResults)) {
    const path = String(hit["path"] ?? hit["target_file"] ?? hit["name"] ?? "");
    if (!path) continue;
    let lineNo = Number(hit["line_number"] ?? hit["line"] ?? hit["lineNumber"] ?? 1);
    if (!Number.isFinite(lineNo)) lineNo = 1;
    let ex = String(hit["excerpt"] ?? hit["content"] ?? "");
    if (!ex) {
      try {
        const src = await studio.scriptRead(path);
        const full = stripLinePrefixes(src.text());
        ex = excerpt(full, lineNo, contextLines);
      } catch { ex = ""; }
    }
    enriched.push({ path, line_number: lineNo, excerpt: ex, context_lines: contextLines });
  }
  return enriched;
}
