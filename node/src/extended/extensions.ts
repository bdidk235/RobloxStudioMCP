/**
 * Extended Roblox Studio MCP — additional useful extensions.
 *
 * Each extension provides a convenience wrapper over one or more raw Studio
 * MCP tools. They are registered in extendedServer.ts as `extended_*` tools.
 */

import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { GAME_TREE_PREFIX, stripLinePrefixes, pickBracketLevel, luaLongBracket } from "./writer.js";
import { INVALID_ARGUMENT, ToolError } from "./errors.js";
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
 *
 * **Omitting `query` means no name filter, and that is the fix.** This used to
 * pass `keywords: "Script"` when `query` was null, so the *optional* parameter
 * defaulted to a filter: an agent auditing a path for its scripts got back only
 * the ones whose names happened to contain "Script", with no indication
 * anything was excluded. Every legitimate script not matching that substring
 * vanished from an audit with zero signal — found by hostile fuzzing, which
 * asked for "everything under this path" and got a partial answer that looked
 * complete. An empty `keywords` is what the underlying tool takes for "no
 * filter", which is what the schema already promised by making the parameter
 * optional.
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
    keywords: query ?? "",
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
 * `fileType: "script"` reads the file and creates a
 * Script/LocalScript/ModuleScript at `parentPath.assetName`.
 * `fileType: "model"` writes a local .rbxm/.rbxmx base64 payload into a
 * single PluginGuiService scratch ModuleScript, then decodes + deserializes
 * it into `parentPath` (no plugin rights needed).
 * `fileType: "image"` uploads via store_image then insert_asset.
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
    // INVALID_ARGUMENT, deliberately not NOT_FOUND. NOT_FOUND in this
    // vocabulary means "the thing you named is not in the DataModel" - the
    // recovery there is to re-list the DataModel, which is useless when the
    // miss is on the host filesystem and the DataModel was never consulted. The
    // recovery here is to send a path that exists, which is an argument fault
    // like any other. Raising the code rather than hoping `classify` guesses
    // right also stops a reworded message from silently moving the code.
    throw new ToolError(
      INVALID_ARGUMENT,
      "file_path must name an existing local file, got " + JSON.stringify(filePath) +
        ". Read the path back before sending it.",
    );
  }
  if (parentPath && !parentPath.startsWith(GAME_TREE_PREFIX)) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "parent_path must be a game-tree path starting with " +
        JSON.stringify(GAME_TREE_PREFIX) + "; got " + JSON.stringify(parentPath) + ".",
    );
  }

  const fileType = rawFileType.toLowerCase();
  const targetName = assetName ?? path.basename(filePath, path.extname(filePath));
  if (!targetName) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "Could not derive asset_name from file_path " + JSON.stringify(filePath) +
        ". Pass asset_name explicitly.",
    );
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

    const { writeScript } = await import("./writer.js");
    const status = await writeScript(studio, targetPath, content, {
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

  if (fileType === "model") {
    // Import a local .rbxm/.rbxmx file WITHOUT LoadLocalAsset (which needs
    // RobloxScript/plugin security the Assistant's execute_luau lacks).
    // Instead: base64 the bytes client-side, `write` slice 0 into a SINGLE
    // scratch ModuleScript, append the remaining slices via
    // ScriptEditorService:UpdateSourceAsync, then in-Studio read .Source ->
    // EncodingService:Base64Decode -> buffer ->
    // SerializationService:DeserializeInstancesAsync -> parent to parentPath.
    // Proven against live Studio (651KB binary .rbxm + 6.7MB XML .rbxmx).
    // Scratch lives in PluginGuiService: studio-only, never replicates or
    // publishes with the place (unlike ServerStorage).
    const MODEL_SLICE_CHARS = 180 * 1024;
    const SCRATCH_NAME = "RBXImportPayload";
    const SCRATCH_PATH = `game.PluginGuiService.${SCRATCH_NAME}`;
    const { writeScript } = await import("./writer.js");
    let b64: string;
    try {
      b64 = (await fs.readFile(filePath)).toString("base64");
    } catch (e) {
      return {
        status: "insert_failed",
        file_path: filePath,
        note: `model load failed: could not read file: ${String(e).slice(0, 200)}`,
      };
    }
    const slices: string[] = [];
    for (let i = 0; i < b64.length; i += MODEL_SLICE_CHARS) {
      slices.push(b64.slice(i, i + MODEL_SLICE_CHARS));
    }
    try {
      await studio.executeLuau(
        `local old = game.PluginGuiService:FindFirstChild(${JSON.stringify(SCRATCH_NAME)})\n` +
          `if old then old:Destroy() end\n` +
          `return "scratch clean"\n`,
        "Edit",
      );
      await writeScript(studio, SCRATCH_PATH, slices[0], {
        className: "ModuleScript",
        createIfMissing: true,
      });
      for (let i = 1; i < slices.length; i++) {
        const lit = luaLongBracket(slices[i], pickBracketLevel(slices[i]));
        await studio.executeLuau(
          `local target = ${SCRATCH_PATH}\n` +
            `local slice = ${lit}\n` +
            `game:GetService("ScriptEditorService"):UpdateSourceAsync(target, function(old) return old .. slice end)\n` +
            `return "appended " .. #slice\n`,
          "Edit",
        );
      }
      const nameLit = luaLongBracket(targetName, pickBracketLevel(targetName));
      const assemble =
        `local target = ${SCRATCH_PATH}\n` +
        `local data = target.Source\n` +
        `local raw = game:GetService("EncodingService"):Base64Decode(buffer.fromstring(data))\n` +
        `local instances = game:GetService("SerializationService"):DeserializeInstancesAsync(raw)\n` +
        `assert(#instances > 0, "deserialized zero instances")\n` +
        `if #instances == 1 then instances[1].Name = ${nameLit} end\n` +
        `local parent = ${parentPath}\n` +
        `local names = {}\n` +
        `for _, inst in ipairs(instances) do\n` +
        `  inst.Parent = parent\n` +
        `  table.insert(names, inst.ClassName .. ":" .. inst.Name)\n` +
        `end\n` +
        `target:Destroy()\n` +
        `return "imported " .. #instances .. " root(s): " .. table.concat(names, ", ")\n`;
      const result = await studio.executeLuau(assemble, "Edit");
      return {
        status: "inserted",
        file_path: filePath,
        asset_name: targetName,
        parent_path: parentPath,
        result: result.text(),
      };
    } catch (e) {
      return {
        status: "insert_failed",
        file_path: filePath,
        note: `model load failed: ${String(e).slice(0, 300)}. ` +
          `May require file path accessible to Studio.`,
      };
    }
  }

  return {
    status: "unsupported_file_type",
    file_path: filePath,
    note: `file_type '${fileType}' is not supported. Supported: script, model, image`,
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
 * Returns status from writeScript.
 */
export async function createModuleWithDeps(
  studio: RobloxStudio,
  modulePath: string,
  content: string,
  options: CreateModuleOptions = {},
): Promise<string> {
  const { className = "ModuleScript", requireTargetPath = null, requireStatement = null } = options;
  const { writeScript } = await import("./writer.js");

  const status = await writeScript(studio, modulePath, content, {
    className,
    createIfMissing: true,
  });
  if (requireTargetPath && requireStatement) {
    const targetResult = await studio.scriptRead(requireTargetPath);
    const { stripLinePrefixes } = await import("./writer.js");
    const targetSource = stripLinePrefixes(targetResult.text());
    if (!targetSource.includes(requireStatement)) {
      const newSource = `${targetSource.replace(/\n$/, "")}\n${requireStatement}\n`;
      await writeScript(studio, requireTargetPath, newSource);
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
    // INVALID_ARGUMENT rather than NOT_FOUND: see the same decision in
    // `insertAssetFromFile`. The DataModel was never consulted here.
    throw new ToolError(
      INVALID_ARGUMENT,
      "file_path must name an existing local file, got " + JSON.stringify(resolved) + ".",
    );
  }
  // Normalize line endings like Python's universal newlines.
  const code = (await fs.readFile(resolved, encoding)).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!code.trim()) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "file_path names an empty file: " + JSON.stringify(resolved) +
        ". Write the Luau to it before executing.",
    );
  }
  return annotateArrayEscape(await studio.executeLuau(code, datamodelType));
}

/**
 * What Studio's serialiser does to an array, measured live 2026-09-30.
 *
 * | Luau returned | arrived as |
 * |---|---|
 * | `{10,20,30}` | `{"1":10,"2":20,"3":30}` |
 * | `{rows={{n=1,v=10},…}}` | `{"rows":{"1":{…},"2":{…}}}` |
 * | `{p=Vector2.new(3,4)}` | `{"p":"3, 4"}` |
 * | `JSONEncode({10,20,30})` | `{"json":"[10,20,30]"}` — intact |
 */
const ARRAY_ESCAPE_NOTE =
  "Array-shaped values do not survive this transport. Studio stringifies integer " +
  'keys, so a Luau array {10,20,30} arrives as an object with keys "1","2","3" ' +
  'rather than [10,20,30], and Vector2.new(3,4) arrives as the single string ' +
  '"3, 4". Reported at: %s. This is a NOTE and nothing was rewritten: a table ' +
  'with genuine string keys "1","2" produces the identical bytes, so repairing ' +
  "it would corrupt real data. Serialise at the source - " +
  "HttpService:JSONEncode - and read the string, which survives intact.";

/**
 * Where a Luau array most likely arrived as a string-keyed object.
 *
 * The signature is an object whose keys are exactly `"1".."n"`, n >= 2, and
 * nothing else. **This cannot be repaired, only reported**, and that is the point
 * of returning a note rather than fixing it:
 *
 * * `{[1]=x,[2]=y}` is a genuine Luau **array** and should have been a list
 * * `{["1"]=x,["2"]=y}` is a genuine **string-keyed map**, already correct as-is
 *
 * Both serialise to identical bytes. Any heuristic that "fixes" the first
 * silently corrupts the second, and any hard failure fires on the second. The
 * only honest move is to say the shape is present and let the caller decide at
 * the source, where the distinction still exists.
 *
 * Originating report: `REQUEST-luau-return-shapes.md` in this repo, where a
 * 165-row driver returned `{}` with no error because of exactly this.
 */
export function arrayEscapePaths(value: unknown, path = ""): string[] {
  const found: string[] = [];
  if (Array.isArray(value)) {
    value.forEach((item, i) => found.push(...arrayEscapePaths(item, `${path}[${i}]`)));
    return found;
  }
  if (value === null || typeof value !== "object") return found;
  const entries = Object.entries(value as Record<string, unknown>);
  const keys = entries.map(([k]) => k);
  if (keys.length >= 2 && keys.every((k) => /^\d+$/.test(k))) {
    const nums = keys.map(Number).sort((a, b) => a - b);
    if (nums.every((n, i) => n === i + 1)) found.push(path || "(root)");
  }
  for (const [key, item] of entries) {
    found.push(...arrayEscapePaths(item, path ? `${path}.${key}` : key));
  }
  return found;
}

/**
 * Append the array-escape note when the result shows the shape. Never edits it.
 *
 * Additive on purpose: the payload the caller reads is untouched, because the two
 * candidate shapes are indistinguishable and any rewrite is a coin flip.
 */
function annotateArrayEscape(result: CallToolResult): CallToolResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(result.text());
  } catch {
    return result;
  }
  if (parsed === null || typeof parsed !== "object") return result;
  const paths = arrayEscapePaths(parsed);
  if (paths.length === 0) return result;
  const shown =
    paths.slice(0, 5).join(", ") + (paths.length > 5 ? ` (+${paths.length - 5} more)` : "");
  result.content.push({ type: "text", text: ARRAY_ESCAPE_NOTE.replace("%s", shown) });
  return result;
}
