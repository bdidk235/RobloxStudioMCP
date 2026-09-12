/**
 * Extended Roblox Studio MCP — additional useful extensions.
 *
 * Each extension provides a convenience wrapper over one or more raw Studio
 * MCP tools. They are registered in extendedServer.ts as `extended_*` tools.
 */

import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { GAME_TREE_PREFIX, stripLinePrefixes } from "./writer.js";
import type { RobloxStudio } from "../roblox.js";
import { CallToolResult } from "../types.js";

const SCRIPT_CLASSES: ReadonlySet<string> = new Set(["Script", "LocalScript", "ModuleScript"]);

/** Default per-source truncation for scriptSearchAndRead (0 = full source). */
export const DEFAULT_MAX_CHARS_PER_SOURCE = 2000;

export interface ScriptSearchEntry {
  path: string;
  source: string;
  name: unknown;
  /** Total lines of the full source (before truncation). */
  line_count: number;
  /** Whether `source` was truncated to `maxCharsPerSource`. */
  truncated: boolean;
}

async function readOneScript(
  studio: RobloxStudio,
  dotPath: string,
  name: unknown,
  maxChars: number | null,
): Promise<ScriptSearchEntry | null> {
  try {
    const srcResult = await studio.scriptRead(dotPath);
    const full = stripLinePrefixes(srcResult.text());
    const line_count = full === "" ? 0 : full.split("\n").length;
    let source = full;
    let truncated = false;
    if (maxChars !== null && maxChars > 0 && full.length > maxChars) {
      source = full.slice(0, maxChars);
      truncated = true;
    }
    return { path: dotPath, source, name, line_count, truncated };
  } catch {
    return null; // Skip unreadable scripts; caller truncates to max_results.
  }
}

export interface ScriptSearchOptions {
  query?: string | null;
  maxResults?: number;
  /**
   * Max characters of `source` per entry (default 2000). Longer sources are
   * truncated with `truncated: true`. Pass 0 or null for full sources.
   */
  maxCharsPerSource?: number | null;
}

/**
 * Search for scripts under rootPath, then batch-read each source.
 *
 * Returns a list of `{ path, source, name, line_count, truncated }`.
 * Sources are truncated to `maxCharsPerSource` (default 2000) to keep
 * payloads small; `line_count` always reflects the full source. Unreadable
 * scripts are skipped. Returns `[]` when the tree payload cannot be parsed.
 */
export async function scriptSearchAndRead(
  studio: RobloxStudio,
  rootPath: string,
  options: ScriptSearchOptions = {},
): Promise<ScriptSearchEntry[]> {
  const { query = null, maxResults = 10, maxCharsPerSource = DEFAULT_MAX_CHARS_PER_SOURCE } = options;
  // Use searchGameTree to find scripts (requires datamodel_type for Edit mode).
  const treeResult = await studio.searchGameTree({
    path: rootPath,
    datamodel_type: "Edit",
    keywords: query === null ? "Script" : query,
  });
  let treeData: unknown;
  try {
    treeData = JSON.parse(treeResult.text());
  } catch {
    return [];
  }
  // Some servers wrap the list in a dict (e.g. {"instances": [...]}).
  if (treeData !== null && typeof treeData === "object" && !Array.isArray(treeData)) {
    for (const key of ["instances", "data", "result", "children"]) {
      const value = (treeData as Record<string, unknown>)[key];
      if (Array.isArray(value)) {
        treeData = value;
        break;
      }
    }
  }
  if (!Array.isArray(treeData)) {
    return [];
  }

  const candidates: Array<[string, unknown]> = [];
  for (const item of treeData as Record<string, unknown>[]) {
    if (typeof item !== "object" || item === null) {
      continue;
    }
    if (!SCRIPT_CLASSES.has(item["className"] as string)) {
      continue;
    }
    const fullPath = item["fullPath"];
    if (typeof fullPath === "string" && fullPath) {
      candidates.push([fullPath, item["name"]]);
    }
    if (candidates.length >= maxResults) {
      break;
    }
  }

  // Batch-read concurrently (bounded to 5 at a time) instead of one
  // round-trip at a time.
  const out: ScriptSearchEntry[] = [];
  for (let i = 0; i < candidates.length; i += 5) {
    const chunk = candidates.slice(i, i + 5);
    const gathered = await Promise.all(chunk.map(([p, n]) => readOneScript(studio, p, n, maxCharsPerSource)));
    for (const r of gathered) {
      if (r !== null) {
        out.push(r);
      }
    }
  }
  return out;
}

export interface InsertAssetOptions {
  fileType?: string;
  assetName?: string | null;
  parentPath?: string;
  className?: string;
}

export type InsertAssetResult = Record<string, unknown>;

/**
 * Insert a local file into the game tree.
 *
 * `fileType: "image"` uploads via store_image then insert_asset.
 * `fileType: "script"` reads the file and creates a
 * Script/LocalScript/ModuleScript at `parentPath.assetName`.
 */
export async function insertAssetFromFile(
  studio: RobloxStudio,
  filePath: string,
  options: InsertAssetOptions = {},
): Promise<InsertAssetResult> {
  const { fileType: rawFileType = "script", assetName = null, parentPath = "game.Workspace", className = "Script" } = options;
  let statOk = false;
  try {
    const st = await fs.stat(filePath);
    statOk = st.isFile();
  } catch {
    statOk = false;
  }
  if (!statOk) {
    throw new Error(`Local file not found: ${filePath}`);
  }
  if (parentPath && !parentPath.startsWith(GAME_TREE_PREFIX)) {
    throw new Error(
      `parentPath must be a game-tree path starting with ${JSON.stringify(GAME_TREE_PREFIX)}; got ${JSON.stringify(parentPath)}.`,
    );
  }

  const fileType = rawFileType.toLowerCase();
  const targetName = assetName ?? path.basename(filePath, path.extname(filePath));
  if (!targetName) {
    throw new Error(`Could not derive asset name from filePath ${JSON.stringify(filePath)}.`);
  }

  if (fileType === "image") {
    // Step 1: Upload via store_image
    let storeText: string;
    try {
      const storeResult = await studio.call("store_image", { filePath });
      storeText = storeResult.text();
    } catch (e) {
      return {
        status: "store_image_failed",
        file_path: filePath,
        note: `store_image call failed: ${String(e).slice(0, 200)}`,
      };
    }

    const match = /IMAGEID_([A-Za-z0-9_-]+)/.exec(storeText);
    if (!match) {
      return {
        status: "upload_parsing_failed",
        file_path: filePath,
        note: `Could not parse image ID from store_image response: ${storeText.slice(0, 200)}`,
      };
    }

    const imageId = match[1];
    const insertKwargs: Record<string, unknown> = { assetId: imageId, assetType: "Image" };
    if (assetName) {
      insertKwargs["assetName"] = assetName;
    }
    if (parentPath) {
      insertKwargs["parentPath"] = parentPath;
    }

    try {
      const insertResult = await studio.call("insert_asset", insertKwargs);
      return {
        status: "inserted",
        file_path: filePath,
        image_id: imageId,
        asset_name: assetName,
        parent_path: parentPath,
        result: insertResult.text(),
      };
    } catch (e) {
      return {
        status: "insert_failed",
        file_path: filePath,
        image_id: imageId,
        note:
          `insert_asset failed: ${String(e).slice(0, 300)}. ` +
          "May require asset in inventory or Studio MCP permissions.",
      };
    }
  }

  if (fileType === "script") {
    // Normalize line endings like Python's universal newlines, so CRLF files
    // produce identical Sources regardless of which client imports them.
    const content = (await fs.readFile(filePath, "utf-8")).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    // Determine the target path in the game tree
    const targetPath = parentPath ? `${parentPath.replace(/\.+$/, "")}.${targetName}` : targetName;

    const { writeLikeMultiEdit } = await import("./writer.js");
    const status = await writeLikeMultiEdit(studio, targetPath, content, {
      className,
      createIfMissing: true,
    });
    return {
      status,
      file_path: filePath,
      target_path: targetPath,
      asset_name: targetName,
      parent_path: parentPath,
      className,
    };
  }

  return {
    status: "unsupported_file_type",
    file_path: filePath,
    note: `file_type '${fileType}' is not supported. Supported: image, script`,
  };
}

/**
 * Stateful watch that remembers console output between polls.
 *
 * Assumes logs are append-only. Handles duplicate lines by matching on the
 * last occurrence, and log truncation/rotation by falling back to the full
 * current buffer.
 */
export class ConsoleWatch {
  static readonly FIRST_CALL_TAIL = 20;
  static readonly MAX_NEW_LINES = 200;

  private lastLines: string[] = [];

  /** Test hook: seed previous lines. */
  _setLastLinesForTests(lines: string[]): void {
    this.lastLines = [...lines];
  }

  private diff(current: string[]): string[] {
    const previous = this.lastLines;
    if (previous.length === 0) {
      return current.slice(-ConsoleWatch.FIRST_CALL_TAIL);
    }
    // Fast path: previous buffer is an exact prefix of current (append-only).
    if (
      current.length >= previous.length &&
      previous.every((line, idx) => current[idx] === line)
    ) {
      return current.slice(previous.length);
    }
    // Slow path: find the last occurrence of the previous tail to tolerate
    // duplicate lines. If not found, logs were truncated — return all.
    const last = previous[previous.length - 1];
    for (let i = current.length - 1; i >= 0; i--) {
      if (current[i] === last) {
        // Prefer the occurrence that leaves a plausible tail; take the last one.
        return current.slice(i + 1);
      }
    }
    return [...current];
  }

  async poll(studio: RobloxStudio): Promise<{ newLines: string[]; totalLines: number; lastLine: string | null }> {
    const result = await studio.call("get_console_output", {});
    const lines = result.text().split("\n").filter((_, idx, arr) => !(arr.length === 1 && arr[0] === "") || true);
    // Match Python: "".splitlines() on "" gives []; Node "".split("\n") gives [""].
    const normalized = result.text() === "" ? [] : result.text().split("\n");

    const newLines = this.diff(normalized).slice(-ConsoleWatch.MAX_NEW_LINES);
    this.lastLines = normalized;
    void lines;
    return {
      newLines,
      totalLines: normalized.length,
      lastLine: normalized.length > 0 ? normalized[normalized.length - 1] : null,
    };
  }
}

// Persistent watch states keyed by studio target, so the MCP server tool
// (which gets a fresh call each time) still returns only *new* lines.
const WATCH_STATES = new Map<string, ConsoleWatch>();

/** Return (creating if needed) the persistent watch state for `key`. */
export function getWatchState(key = "default"): ConsoleWatch {
  let watch = WATCH_STATES.get(key);
  if (!watch) {
    watch = new ConsoleWatch();
    WATCH_STATES.set(key, watch);
  }
  return watch;
}

/** Test hook: clear persistent watch states. */
export function __clearWatchStatesForTests(): void {
  WATCH_STATES.clear();
}

/**
 * Poll console output and return only new lines since last call.
 *
 * When `watchState` is omitted a process-wide default is reused so
 * consecutive calls only return fresh lines. Pass an explicit
 * {@link ConsoleWatch} for isolated/per-studio tracking.
 */
export async function watchOutput(
  studio: RobloxStudio,
  watchState?: ConsoleWatch,
): Promise<{ newLines: string[]; totalLines: number; lastLine: string | null }> {
  const state = watchState ?? getWatchState(String(studio.studioId ?? "default"));
  return state.poll(studio);
}

export interface CreateModuleOptions {
  className?: string;
  requireTargetPath?: string | null;
  requireStatement?: string | null;
}

/**
 * Create a new ModuleScript and optionally wire it into a target script.
 *
 * When `requireTargetPath` + `requireStatement` are given, the statement is
 * appended to the target (once) if not already present.
 *
 * Returns status from writeLikeMultiEdit.
 */
export async function createModuleWithDeps(
  studio: RobloxStudio,
  modulePath: string,
  content: string,
  options: CreateModuleOptions = {},
): Promise<string> {
  const { className = "ModuleScript", requireTargetPath = null, requireStatement = null } = options;
  const { writeLikeMultiEdit } = await import("./writer.js");

  const status = await writeLikeMultiEdit(studio, modulePath, content, {
    className,
    createIfMissing: true,
  });
  if (requireTargetPath && requireStatement) {
    const targetResult = await studio.scriptRead(requireTargetPath);
    const { stripLinePrefixes } = await import("./writer.js");
    const targetSource = stripLinePrefixes(targetResult.text());
    if (!targetSource.includes(requireStatement)) {
      const newSource = `${targetSource.replace(/\n$/, "")}\n${requireStatement}\n`;
      await writeLikeMultiEdit(studio, requireTargetPath, newSource);
    }
  }
  return status;
}

const ERROR_HINTS = ["error", "failed", "stack trace", "exception"];

export function isErrorLine(line: string): boolean {
  const lowered = line.toLowerCase();
  // Ignore benign "0 errors" / "0 failed" summaries.
  if (lowered.includes("0 error") || lowered.includes("0 fail")) {
    return false;
  }
  return ERROR_HINTS.some((hint) => lowered.includes(hint));
}

export interface RunTestsOptions {
  testPaths?: string[] | null;
  waitSeconds?: number;
  maxLines?: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Run play testing and collect output as a test summary.
 *
 * Returns `{ passed, consoleLines, errors }`.
 */
export async function runTests(
  studio: RobloxStudio,
  options: RunTestsOptions = {},
): Promise<{ passed: boolean; consoleLines: string[]; errors: string[] }> {
  const { testPaths = null, waitSeconds = 2.0, maxLines = 30 } = options;
  const missing: string[] = [];
  if (testPaths) {
    for (const p of testPaths) {
      try {
        await studio.scriptRead(p);
      } catch (exc) {
        missing.push(`${p}: ${exc}`);
      }
    }
  }

  await studio.startPlay();
  try {
    // Poll until output stabilises or the budget runs out, instead of a
    // single fixed sleep (flaky on slow places).
    let consoleText = "";
    const interval = 0.5;
    let elapsed = 0.0;
    let lastSnapshot = "";
    for (;;) {
      await sleep(Math.min(interval, Math.max(0.0, waitSeconds - elapsed)) * 1000);
      elapsed += interval;
      const consoleResult = await studio.call("get_console_output", {});
      consoleText = consoleResult.text();
      if (consoleText === lastSnapshot || elapsed >= waitSeconds) {
        break;
      }
      lastSnapshot = consoleText;
    }
    const lines = consoleText ? consoleText.split("\n") : [];
    const errors = lines.filter((line) => isErrorLine(line));
    const passed = errors.length === 0 && missing.length === 0;
    return {
      passed,
      consoleLines: lines.length > 0 ? lines.slice(-maxLines) : [],
      errors: [...errors, ...missing.map((m) => `Missing test script: ${m}`)],
    };
  } finally {
    await studio.stopPlay();
  }
}

export interface ExecuteFromFileOptions {
  datamodelType?: string;
  encoding?: BufferEncoding;
}

function expandHome(filePath: string): string {
  if (filePath === "~" || filePath.startsWith("~/") || filePath.startsWith("~\\")) {
    return path.join(os.homedir(), filePath.slice(2));
  }
  return filePath;
}

/**
 * Execute Luau source read from a local file.
 *
 * Same as `RobloxStudio.executeLuau`, but the code comes from a
 * `.luau`/`.lua` file on disk instead of an inline string.
 */
export async function executeLuauFromFile(
  studio: RobloxStudio,
  filePath: string,
  options: ExecuteFromFileOptions = {},
): Promise<CallToolResult> {
  const { datamodelType = "Edit", encoding = "utf-8" } = options;
  const resolved = path.resolve(expandHome(filePath));
  let statOk = false;
  try {
    statOk = (await fs.stat(resolved)).isFile();
  } catch {
    statOk = false;
  }
  if (!statOk) {
    throw new Error(`Luau file not found: ${resolved}`);
  }
  // Normalize line endings like Python's universal newlines.
  const code = (await fs.readFile(resolved, encoding)).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!code.trim()) {
    throw new Error(`Luau file is empty: ${resolved}`);
  }
  return studio.executeLuau(code, datamodelType);
}
