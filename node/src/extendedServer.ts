/**
 * Extended stdio MCP server that wraps the Studio MCP with write/update tools.
 *
 * A thin layer over the standard Studio MCP proxy that adds convenience
 * tools on top of the raw Studio MCP tools (see EXTENDED_TOOLS):
 *
 * - `extended_write_script` — full-body script replacement
 * - `extended_update_script` — batch edits with graceful skipping
 * - `extended_script_search_and_read`, `extended_insert_asset_from_file`,
 *   `extended_watch_output`, `extended_run_tests`,
 *   `extended_execute_luau_from_file`
 *
 * Run as a stdio server:
 *
 *     node ./dist/extendedServer.js
 */

import { createInterface } from "node:readline";
import { MCPClient } from "./client.js";
import { RobloxStudio, defaultArgs, defaultCommand, defaultShell } from "./roblox.js";
import { Tool } from "./types.js";
import type { SendFn } from "./server.js";
import { sendMessage as baseSend } from "./server.js";
import { writeScript } from "./extended/writer.js";
import { updateScript, type UpdateResult } from "./extended/updater.js";
import { AMBIGUOUS_STUDIO, INVALID_ARGUMENT, LAUNCH_FAILED, ToolError, classify, describe } from "./extended/errors.js";

// ---------------------------------------------------------------------------
// Extended tool definitions (for tools/list)
// ---------------------------------------------------------------------------

/**
 * Relayed tools with a better `extended_*` equivalent, and the steer appended
 * to each relayed description. Mirrors `_STEERS` in
 * `python/src/roblox_studio_mcp/extended_server.py` and `_STEERS` in
 * `parity/build_contract.py`; all three are asserted equal by both suites,
 * because a drift here is a model quietly using the worse tool forever and
 * nothing anywhere reports it.
 */
export const STEERS: Readonly<Record<string, string>> = {
  multi_edit:
    "NOTE: prefer extended_write_script for a full-file replacement, or extended_update_script for targeted edits - this fails the whole call if any one edit is invalid, and does not read the target first.",
  screen_capture:
    "NOTE: prefer extended_capture when the pixels are the measurement - this is JPEG (smears 1px edges); extended_capture names the studio_id it captured and can save to a file instead of returning megabytes.",
  script_grep:
    "NOTE: prefer extended_script_grep - this returns no context lines, so each hit costs a separate script_read to see.",
  get_console_output:
    "NOTE: prefer extended_watch_output when polling - this returns the whole buffer every time, re-sending lines you already have.",
  script_search:
    "NOTE: prefer extended_script_search_and_read to also batch-read the sources; this returns paths only.",
};

/**
 * Refuse `screen_capture` when the target is ambiguous. Request P0.1.
 *
 * Measured 2026-10-01, three Studios attached: the schema **requires**
 * `studio_id` and refuses the call without it, so this guard is defence-in-depth
 * rather than the primary defence. Over MCP, schema validation runs before
 * dispatch and an omitted id never arrives here.
 *
 * It still earns its place on the paths that do no validation: a direct
 * `client.call_tool`, and this repo's own `client.ts` validates no schema at
 * all. Whether an older Studio build made the id optional is unmeasured.
 *
 * Historical reason the guard exists: a measured failure where probes ran
 * against one place and every capture came back as an empty baseplate. That is
 * no longer explained by the optional-id mechanism this guard was built for,
 * so it is recorded as unexplained rather than as a live hazard.
 *
 * With exactly one Studio attached this passes through unchanged. With more, it
 * raises and names them. It never picks one.
 *
 * `notes` collects non-fatal findings for the caller: if the mesh cannot be
 * read, refusing would block a call that may well have been unambiguous, so the
 * call passes and the reason is reported instead.
 */
async function guardScreenCapture(
  client: MCPClient,
  args: Record<string, unknown>,
  notes: string[],
): Promise<void> {
  if (args["studio_id"]) return;
  let studios: Array<Record<string, unknown>>;
  try {
    // `.json()`, not the raw result: `callTool` returns a CallToolResult, and
    // an earlier version of this read it as a bare array. That produced a
    // nonsense candidate list rather than an error, which is the worst shape
    // for a guard - it would have refused on garbage.
    const result = await client.callTool("list_roblox_studios", {});
    const parsed = result.json();
    studios = Array.isArray(parsed)
      ? (parsed as Array<Record<string, unknown>>)
      : (((parsed as { instances?: unknown })?.instances ?? []) as Array<
          Record<string, unknown>
        >);
  } catch (exc) {
    notes.push(
      `Could not read the Studio list to check the capture target (${String(exc)}); ` +
        "passing through, so the capture may be of the wrong Studio.",
    );
    return;
  }
  if (studios.length <= 1) return;
  throw new ToolError(
    AMBIGUOUS_STUDIO,
    `screen_capture needs a studio_id, and with ${studios.length} Studios attached ` +
      "an omitted one has no single right answer - pass studio_id, or use " +
      "extended_capture which reports the Studio it captured.",
    {
      candidates: studios.map((s) => ({
        studio_id: String(s["id"] ?? s["studio_id"] ?? ""),
        name: s["name"] ?? null,
      })),
    },
  );
}

/**
 * Guards on RELAYED tools: they run before pass-through and may refuse. Not a
 * rewrite - the call is forwarded untouched when it passes - but the proxy does
 * hold the arguments. Mirrors `_RELAY_GUARDS` in the Python server.
 */
export const RELAY_GUARDS: Readonly<
  Record<
    string,
    (
      client: MCPClient,
      args: Record<string, unknown>,
      notes: string[],
    ) => Promise<void>
  >
> = { screen_capture: guardScreenCapture };

export const EXTENDED_TOOLS: Tool[] = [
  new Tool(
    "extended_write_script",
    "Replace a game-tree script's whole body. Atomic, and returns \"unchanged\" without writing when the content already matches. Prefer over raw multi_edit, which fails the entire call if any one part is invalid.",
    {
      type: "object",
      properties: {
        target_path: {
          type: "string",
          description: "DataModel dot-path (e.g. game.ServerScriptService.MyScript). Must start with \"game.\".",
        },
        content: { type: "string", description: "The new full text of the script." },
        className: {
          type: "string",
          default: "Script",
          description: "Roblox class when creating a new script (Script, LocalScript, ModuleScript). Only used with create_if_missing. Default: Script.",
        },
        create_if_missing: {
          type: "boolean",
          default: false,
          description: "If true, create the script via execute_luau when it doesn't exist yet. If false (default), a missing script is an error.",
        },
        studio_id: { type: "string", description: "Optional explicit studio_id to target. Auto-detected if omitted." },
      },
      required: ["target_path", "content"],
    },
  ),
  new Tool(
    "extended_update_script",
    "Batch exact string replacements in a game-tree script, in order, each on the result of the last.  Unlike raw multi_edit, one bad edit does not sink the others: missing, ambiguous and no-op edits are skipped with warnings unless you make them strict.",
    {
      type: "object",
      properties: {
        target_path: { type: "string", description: "DataModel dot-path (e.g. game.ServerScriptService.MyScript). Must start with \"game.\"." },
        edits: {
          type: "array",
          description: "List of edit objects. Edits are applied in order; each operates on the result of the previous edit. Edits whose old_string is not found or matches new_string are skipped with a warning by default.",
          items: {
            type: "object",
            properties: {
              old_string: { type: "string", description: "Exact text to match (must appear exactly once unless replace_all is true)." },
              new_string: { type: "string", description: "Replacement text (must differ from old_string)." },
              replace_all: {
                type: "boolean",
                default: false,
                description: "Like Edit's replaceAll: replace every occurrence of old_string instead of requiring exactly one match.",
              },
              replaceAll: {
                type: "boolean",
                default: false,
                description: "Alias of replace_all.",
              },
            },
            required: ["old_string", "new_string"],
          },
        },
        skip_missing: {
          type: "boolean",
          default: true,
          description: "If true (default), skip edits whose old_string is missing or ambiguous. If false, raise an Edit-like error instead.",
        },
        skip_no_ops: {
          type: "boolean",
          default: true,
          description: "If true (default), skip edits where old_string == new_string. If false, raise an error instead.",
        },
        studio_id: { type: "string", description: "Optional explicit studio_id to target. Auto-detected if omitted." },
      },
      required: ["target_path", "edits"],
    },
  ),
  new Tool(
    "extended_script_grep",
    "Search script contents with surrounding context, in file order (not ranked). Substring by default, regex with regex:true. Prefer over raw script_grep, which returns no context.",
    {
      type: "object",
      properties: {
        query: { type: "string", description: "Substring or regex to grep for (depending on regex flag)." },
        root_path: { type: "string", description: "Dot-path to start from (e.g. game.ServerScriptService)." },
        context_lines: { type: "integer", default: 3, description: "Number of context lines around each hit (0-10, default 3)." },
        regex: { type: "boolean", default: false, description: "When true, treat query as a Luau/PCRE regex." },
        instance_type: { type: "string", description: "Only match Luau containers of this Instance type." },
        max_results: { type: "integer", default: 30, description: "Cap on returned hits (1-100, default 30)." },
        studio_id: { type: "string", description: "Optional explicit studio_id to target. Auto-detected if omitted." },
      },
      required: ["query"],
    },
  ),
  new Tool(
    "extended_script_search_and_read",
    "Find scripts under a DataModel path and batch-read their sources. Sources truncate at max_chars_per_source, so pass 0 when you need them whole.",
    {
      type: "object",
      properties: {
        root_path: { type: "string", description: "DataModel path (e.g. game.ServerScriptService)." },
        query: { type: "string", description: "Optional keyword filter for script names." },
        max_results: { type: "integer", default: 10, description: "Max number of results to return." },
        max_chars_per_source: { type: "integer", default: 2000, description: "Max characters of source per entry. Longer sources are truncated with truncated:true. Pass 0 for full sources." },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
      required: ["root_path"],
    },
  ),
  new Tool(
    "extended_insert_asset_from_file",
    "Insert a local script, model, or image file into the game tree.",
    {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Local file path." },
        file_type: {
          type: "string",
          enum: ["script", "model", "image"],
          default: "script",
          description: "Type of file: 'script' (luau/lua), 'model' (rbxm/rbxmx), or 'image' (png/jpg/jpeg).",
        },
        asset_name: { type: "string", description: "Name to give the inserted instance (defaults to file basename)." },
        parent_path: { type: "string", default: "game.Workspace", description: "Container path in the DataModel (e.g. game.ReplicatedStorage)." },
        className: {
          type: "string",
          default: "Script",
          description: "Roblox class for script files (Script, LocalScript, ModuleScript). Only used when file_type='script'. Default: Script.",
        },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
      required: ["file_path"],
    },
  ),
  new Tool(
    "extended_watch_output",
    "New console lines since your last call, filtered by pattern and capped by max_lines, so you get what matters instead of the whole buffer.  Use this instead of get_console_output in a loop.",
    {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Only return lines matching this regex." },
        max_lines: { type: "integer", default: 200, description: "Cap on returned lines, keeping the newest." },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
    },
  ),
  new Tool(
    "extended_run_tests",
    "Play the place; console as {passed, console_lines, errors}. Green-check only. See rsx-playtest.",
    {
      type: "object",
      properties: {
        test_paths: { type: "array", items: { type: "string" }, description: "Optional list of test script paths." },
        wait_seconds: { type: "number", default: 2.0, description: "How long to let play-mode output accumulate." },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
    },
  ),
  new Tool(
    "extended_breakpoints",
    "Set or remove one breakpoint on a running server script. Each hit prints one console line; read them with extended_watch_output.  log_expression MUST fail, or no hit is reported.  Toggles.  See the rsx-breakpoints skill.",
    {
      type: "object",
      properties: {
        script_path: {
          type: "string",
          description: "Script name under ServerScriptService, e.g. 'BpTest'.",
        },
        line: {
          type: "integer",
          description: "1-based source line.",
        },
        log_expression: {
          type: "string",
          description:
            "Luau expression run on every hit; defaults to a bare hit marker.  See the rsx-breakpoints skill.",
        },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
      // No `action`. The registry knows whether this location already has a
      // breakpoint, so the caller says "here" rather than restating the state -
      // which is why this is a branch and not a required enum. `list` and
      // `clear` are `extended_clear_breakpoints`. Node previously carried an
      // `action` enum and *no* `required` list at all, so a call with no
      // script_path was accepted by the schema and failed at runtime; the
      // contract in parity/tools.json is what caught it.
      required: ["script_path", "line"],
    },
  ),
  new Tool(
    "extended_clear_breakpoints",
    "Remove every breakpoint and clear the registry.  Needs a play session.  See the rsx-breakpoints skill.",
    {
      type: "object",
      properties: {
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
    },
  ),
  new Tool(
    "extended_manage_instance",
    "Studio instances and places.  list -> mesh[].studio_id per attached Studio (unjoined: pair a process's place_file with a mesh name yourself).  launch -> the studio_id it opened, pass it on.  places -> candidate paths + place_id.  make_place -> a throwaway path to launch.  stop -> TERMINATES a process, irreversibly.  Launch needs a place; a Studio joins the mesh only with one open.",
    {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["list", "launch", "stop", "places", "make_place"],
          description: "What to do.",
        },
        studio_id: {
          type: "string",
          description: "For stop: which Studio to terminate.",
        },
        place_path: {
          type: "string",
          description: "For launch: the place file to open.",
        },
      },
      required: ["action"],
    },
  ),
  new Tool(
    "extended_wait_for",
    "Wait until a Luau condition is true, polling host-side, then return a verdict with the last value.  Use it when a result takes time: you cannot sleep between calls.  Return true, false or nil; anything else errors - 0 is truthy in Lua.",
    {
      type: "object",
      properties: {
        condition: {
          type: "string",
          description:
            "Luau expression, e.g. '#game:GetService(\"Players\"):GetPlayers() >= 3'.  true, false or nil; anything else is an error.",
        },
        timeout_seconds: {
          type: "number",
          default: 30.0,
          description: "How long to wait. Capped at 90.",
        },
        datamodel_type: {
          type: "string",
          default: "Edit",
          description: "DataModel to evaluate in (Edit, Client, Server).",
        },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
      required: ["condition"],
    },
  ),
  new Tool(
    "extended_skill",
    "Fetch a transport skill from the repo's skills/ folder; omit skill_name for the index.  Read one before driving the transport: these cover its traps, not the engine.",
    {
      type: "object",
      properties: {
        skill_name: { type: "string", description: "Skill to fetch. Omit to list what is available." },
      },
    },
  ),
  new Tool(
    "extended_capture",
    "Lossless PNG of the viewport, for when the pixels are the measurement.  screen_capture is JPEG-only and smears 1px edges: use it for a look, this to measure.  Costs seconds and megabytes.  See the rsx-capture skill.",
    {
      type: "object",
      properties: {
        save_path: {
          type: "string",
          description:
            "Local path to write the PNG to. Omit to receive the PNG as base64 in the response instead, which is large (roughly 1 KB per 1000 px of viewport).",
        },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
    },
  ),
  new Tool(
    "extended_studio_identity",
    "This Studio's game:GetDebugId(). Separates two Studios on one place; does NOT survive a restart, so it is session-scoped.  Edit mode only.  See the rsx-targeting skill.",
    {
      type: "object",
      properties: {
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
    },
  ),
  new Tool(
    "extended_list_studios",
    "List connected Studios, each with its current studio_id and debug_id.  Neither is durable: both change on restart, so this tracks a place, not an instance.  See the rsx-targeting skill.",
    {
      type: "object",
      properties: {
        refresh: {
          type: "boolean",
          default: true,
          description:
            "Re-query Studio and re-read each GetDebugId. Set false to report the registry as-is, with no round trip.",
        },
        resolve: {
          type: "object",
          description:
            "Look one registered instance up instead of listing. Give debug_id for an exact match, or name/place_id together. Ambiguous selectors return every candidate rather than guessing.",
          properties: {
            debug_id: { type: "string" },
            name: { type: "string" },
            place_id: { type: "number" },
          },
        },
      },
    },
  ),
  new Tool(
    "extended_execute_luau_from_file",
    "execute_luau with the code read from a local .luau file, for anything too long to inline in a tool call.",
    {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Local .luau/.lua file to execute." },
        datamodel_type: { type: "string", default: "Edit", description: "DataModel to run in (Edit, Client, Server)." },
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
      required: ["file_path"],
    },
  ),
];

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

function requireStr(args: Record<string, unknown>, field: string): string {
  const value = args[field];
  if (typeof value !== "string" || value.length === 0) {
    // The received value is in the message because "must be a non-empty
    // string" alone cannot tell the caller whether it sent "", 42 or null, and
    // those three need three different fixes. Node previously said "Missing
    // required string argument" with no value at all, and Python said
    // something else again, for the same request.
    throw new ToolError(
      INVALID_ARGUMENT,
      field + " must be a non-empty string, got " + describe(value) + ".",
    );
  }
  return value;
}


/**
 * Parse the `line` argument, refusing it here rather than in `setBreakpoint`.
 *
 * `Math.trunc(Number("x"))` is `NaN`, and `NaN` reached Studio: every
 * downstream comparison against it is false, so the guard that was supposed to
 * reject it never fired. Python raised a bare `ValueError` that classified as
 * `UNKNOWN` for the same input. Both now refuse here, identically, with the
 * received value attached.
 */
function parseLine(raw: unknown): number {
  if (raw === undefined || raw === null) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "line must be a 1-based positive integer, got " + describe(raw) + ".",
    );
  }
  const line = Math.trunc(Number(raw));
  if (!Number.isFinite(line)) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "line must be a 1-based positive integer, got " + describe(raw) + ".",
    );
  }
  return line;
}

/**
 * Read a snake_case key, falling back to its camelCase alias.
 *
 * Key *presence*, not truthiness. `??` treats an explicit `new_string: null` as
 * absent and falls through to the alias, so the error said "got undefined"
 * where Python said "got null" - the same request, two different error
 * strings, and a `null` is exactly the value a caller most needs identified.
 * Python's `dict.get(key, default)` only falls back on a missing key, so this
 * matches it.
 */
function pickAlias(d: Record<string, unknown>, snake: string, camel: string): unknown {
  return Object.prototype.hasOwnProperty.call(d, snake) ? d[snake] : d[camel];
}

type ExtendedResult = Record<string, unknown>;

function okText(text: string): ExtendedResult {
  // `isError` is the MCP-spec key; `is_error` is kept for interop with the
  // Python implementation's CallToolResult parser expectations.
  return { content: [{ type: "text", text }], isError: false, is_error: false };
}

async function callExtendedWrite(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const targetPath = requireStr(args, "target_path");
  const content = args["content"];
  if (typeof content !== "string") {
    // Empty content is legal here - it blanks a script - so only the type is
    // checked, and the message says which type.
    throw new ToolError(
      INVALID_ARGUMENT,
      'content must be a string, got ' + describe(content) + '. Use "" to blank a script.',
    );
  }
  const className = (args["className"] as string | undefined) ?? null;
  const createIfMissing = Boolean(args["create_if_missing"] ?? false);
  const studioId = (args["studio_id"] as string | undefined) ?? null;

  const studio = new RobloxStudio(client, studioId);
  const status = await writeScript(studio, targetPath, content, {
    className,
    createIfMissing,
  });
  return okText(status);
}

async function callExtendedUpdate(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const targetPath = requireStr(args, "target_path");
  const editsRaw = args["edits"];
  if (!Array.isArray(editsRaw) || editsRaw.length === 0) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "edits must be a non-empty list, got " +
        describe(editsRaw) +
        '. Send [{"old_string": ..., "new_string": ...}].',
    );
  }
  const skipMissing = Boolean(args["skip_missing"] ?? true);
  const skipNoOps = Boolean(args["skip_no_ops"] ?? true);
  const studioId = (args["studio_id"] as string | undefined) ?? null;

  const edits: Array<{ old_string: string; new_string: string; replace_all?: boolean }> = (
    editsRaw as unknown[]
  ).map((e, index) => {
    // Each message names the offending index and shows the received value.
    // "Each edit must be {old_string, new_string}" is unactionable from a
    // batch of ten: the caller has to re-send to find out which one it was,
    // and "" / 42 / absent all read the same.
    if (typeof e !== "object" || e === null) {
      throw new ToolError(
        INVALID_ARGUMENT,
        "edits[" + index + "] must be an object {old_string, new_string}, got " + describe(e) + ".",
      );
    }
    const d = e as Record<string, unknown>;
    const oldString = pickAlias(d, "old_string", "oldString");
    const newString = pickAlias(d, "new_string", "newString");
    if (typeof oldString !== "string" || oldString.length === 0) {
      throw new ToolError(
        INVALID_ARGUMENT,
        "edits[" + index + "].old_string must be a non-empty string, got " + describe(oldString) + ".",
      );
    }
    if (typeof newString !== "string") {
      throw new ToolError(
        INVALID_ARGUMENT,
        'edits[' + index + '].new_string must be a string (it may be ""), got ' +
          describe(newString) + ".",
      );
    }
    const replaceAll = Boolean(d["replace_all"] ?? d["replaceAll"] ?? false);
    return { old_string: oldString, new_string: newString, replace_all: replaceAll };
  });

  const studio = new RobloxStudio(client, studioId);
  const result: UpdateResult = await updateScript(studio, targetPath, edits, {
    skipMissing,
    skipNoOps,
  });

  let text = String(result);
  if (result.warnings.length > 0) {
    text += "\n" + result.warnings.map((w) => `  WARNING: ${w}`).join("\n");
  }
  return okText(text);
}

async function callScriptGrep(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { extendedScriptGrep } = await import("./extended/grep.js");
  const query = requireStr(args, "query");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const hits = await extendedScriptGrep(studio, query, {
    rootPath: (args["root_path"] as string | undefined) ?? undefined,
    contextLines: args["context_lines"] === undefined ? undefined : Number(args["context_lines"]),
    regex: args["regex"] === undefined ? undefined : Boolean(args["regex"]),
    instanceType: (args["instance_type"] as string | undefined) ?? undefined,
    maxResults: args["max_results"] === undefined ? undefined : Number(args["max_results"]),
  });
  return okText(JSON.stringify(hits, null, 2));
}

async function callScriptSearchRead(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { scriptSearchAndRead } = await import("./extended/extensions.js");
  const rootPath = requireStr(args, "root_path");
  const query = (args["query"] as string | undefined) ?? null;
  const maxResults = Number(args["max_results"] ?? 10);
  const maxCharsRaw = args["max_chars_per_source"] ?? args["maxCharsPerSource"] ?? 2000;
  const maxCharsParsed = Number(maxCharsRaw);
  const maxCharsPerSource = Number.isFinite(maxCharsParsed) ? maxCharsParsed : 2000;
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const results = await scriptSearchAndRead(studio, rootPath, { query, maxResults, maxCharsPerSource });
  return okText(JSON.stringify(results, null, 2));
}

async function callInsertAsset(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { insertAssetFromFile } = await import("./extended/extensions.js");
  const filePath = requireStr(args, "file_path");
  const fileType = String(args["file_type"] ?? "script");
  const assetName = (args["asset_name"] as string | undefined) ?? null;
  const parentPath = String(args["parent_path"] ?? "game.Workspace");
  const className = String(args["className"] ?? "Script");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const result = await insertAssetFromFile(studio, filePath, {
    fileType,
    assetName,
    parentPath,
    className,
  });
  return okText(JSON.stringify(result, null, 2));
}

async function callWatchOutput(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { getWatchState, watchOutput } = await import("./extended/extensions.js");
  const { ToolError, INVALID_ARGUMENT } = await import("./extended/errors.js");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  // Persistent per-studio state so consecutive calls return only new lines.
  const result = await watchOutput(studio, getWatchState(studioId ?? "default"));

  // `watchOutput` returns `newLines` as a real array, already split by the
  // watch, so there is nothing to split here. The literal-`\n` split is a
  // fallback for the raw-client shape, where the console arrives as ONE line
  // with literal escapes and splitting on a real newline finds nothing.
  const newLines = (result as { newLines?: unknown }).newLines;
  const lines = Array.isArray(newLines)
    ? (newLines as unknown[]).map((ln) => String(ln)).filter((ln) => ln.trim().length > 0)
    : String((result as { text?: unknown }).text ?? "")
        .split("\\n")
        .filter((ln: string) => ln.trim().length > 0);

  const pattern = args["pattern"];
  let matched = lines;
  if (pattern !== undefined && pattern !== null && pattern !== "") {
    let rx: RegExp;
    try {
      rx = new RegExp(String(pattern));
    } catch (exc) {
      throw new ToolError(
        INVALID_ARGUMENT,
        `bad pattern: ${exc instanceof Error ? exc.message : String(exc)}`,
      );
    }
    matched = lines.filter((ln) => rx.test(ln));
  }

  const cap = Number(args["max_lines"] ?? 200);
  if (!Number.isFinite(cap) || cap < 1) {
    throw new ToolError(INVALID_ARGUMENT, "max_lines must be at least 1");
  }
  const shown = matched.slice(-Math.floor(cap));

  return okText(
    JSON.stringify(
      {
        text: shown.join("\n"),
        lines: shown,
        returned: shown.length,
        matched: matched.length,
        total_seen: lines.length,
        truncated: shown.length < matched.length,
      },
      null,
      2,
    ),
  );
}

async function callRunTests(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { runTests } = await import("./extended/extensions.js");
  const testPaths = (args["test_paths"] as string[] | undefined) ?? null;
  const waitSeconds = Number(args["wait_seconds"] ?? 2.0);
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const result = await runTests(studio, { testPaths, waitSeconds });
  return okText(JSON.stringify(result, null, 2));
}

async function callExecuteFile(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { executeLuauFromFile } = await import("./extended/extensions.js");
  const filePath = requireStr(args, "file_path");
  const datamodelType = String(args["datamodel_type"] ?? "Edit");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const result = await executeLuauFromFile(studio, filePath, { datamodelType });
  return okText(result.text());
}

async function callStudioIdentity(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { readInBandIdentity } = await import("./extended/registry.js");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const identity = await readInBandIdentity(studio);
  const result: ExtendedResult = {
    identity,
    studio_id: studioId ?? studio.studioId,
    datamodel: "Edit",
    note:
      "GetDebugId identifies the DataModel root, not the process. Read it in Edit " +
      "mode; a play session reports a different value for the same Studio. Whether " +
      "it survives a Studio restart is unverified.",
  };
  if (identity === null) {
    result["warning"] =
      "No tagged identity line in the console. The proxy may have truncated " +
      "output, or the code may not have run.";
  }
  return okText(JSON.stringify(result, null, 2));
}

async function callSkill(_client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { callSkill: run } = await import("./extended/skills.js");
  const raw = args["skill_name"];
  if (raw !== undefined && raw !== null && typeof raw !== "string") {
    throw new ToolError(
      INVALID_ARGUMENT,
      "skill_name must be a string when given, got " +
        describe(raw) +
        ". Omit it for the index of available skills.",
    );
  }
  return okText(run(raw ?? undefined));
}

async function callBreakpoints(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const bp = await import("./extended/breakpoints.js");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);

  // No `action`: the registry tells us whether this location already has a
  // breakpoint, so the caller says "here" rather than restating the state.
  const scriptPath = requireStr(args, "script_path");
  const line = parseLine(args["line"]);

  const existing = (await bp.listBreakpoints(studio)).filter(
    (b) => b.script_path === scriptPath && b.line === line,
  );

  let result: Record<string, unknown>;
  if (existing.length > 0) {
    await bp.removeBreakpoint(studio, scriptPath, line);
    result = { removed: { script_path: scriptPath, line } };
  } else {
    const raw = args["log_expression"];
    result = {
      added: await bp.setBreakpoint(studio, scriptPath, line, {
        logExpression: raw === undefined || raw === null || raw === "" ? undefined : String(raw),
      }),
    };
  }

  result["hit_prefix"] = bp.HIT_PREFIX;
  result["how_to_read_hits"] =
    "extended_watch_output with pattern='^Breakpoint '; each hit is one line.";
  return okText(JSON.stringify(result, null, 2));
}

async function callClearBreakpoints(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const bp = await import("./extended/breakpoints.js");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  await bp.clearBreakpoints(studio);
  return okText(JSON.stringify({ cleared: true }, null, 2));
}

async function callManageInstance(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const inst = await import("./extended/instance.js");
  const action = requireStr(args, "action");

  if (action === "list") {
    // `withAttachment` costs a second PowerShell spawn at ~1.9s measured on
    // this machine, so it is opt-in per action rather than always paid.
    //
    // The mesh rows come back alongside the process rows rather than joined
    // onto them, for the same reason Python does it: the join cannot be
    // trusted. Two Studios on one place both report the name "Place1", so
    // pairing by name is a guess. Handing back both sides lets the caller use
    // the pair when it is unambiguous and see that it is not when it is not.
    //
    // This closed the gap that made `list` useless as a first call: it
    // returned PIDs and no studio_id, so an agent had to follow it with
    // extended_list_studios and correlate the rows itself.
    let mesh: Array<{ studio_id: string; name: unknown }> = [];
    let meshError: string | undefined;
    try {
      const rows = await client.callTool("list_roblox_studios", {});
      const parsed = JSON.parse(rows.text()) as unknown;
      const list = Array.isArray(parsed)
        ? parsed
        : ((parsed as { instances?: unknown[] })?.instances ?? []);
      mesh = (list as Array<Record<string, unknown>>).map((row) => ({
        studio_id: String(row.id ?? row.studio_id ?? row.studioId ?? ""),
        name: row.name ?? null,
      }));
    } catch (exc) {
      meshError = String(exc);
    }
    const payload: Record<string, unknown> = {
      action,
      processes: inst.listStudioProcesses(true),
      mesh,
      note:
        "One place can be many processes: a -task StartServer Studio spawns a -task StartClient Studio per player. " +
        "`processes` are OS processes; `mesh` rows are what the proxy reports, each carrying the studio_id " +
        "every other tool wants. They are NOT joined: pair a process's place_file with a mesh name yourself, " +
        "and treat the pair as unproven when two Studios share one place name.",
      identity_caveat:
        "Node identifies by process enumeration, not by the log-based chain Python uses. " +
        "With two Studios open on the same place it cannot say which is which.",
    };
    if (meshError !== undefined) payload["mesh_error"] = meshError;
    return okText(JSON.stringify(payload, null, 2));
  }

  if (action === "places") {
    return okText(JSON.stringify({ action, places: inst.listPlaceCandidates() }, null, 2));
  }

  if (action === "stop") {
    const studioId = args["studio_id"] as string | undefined;
    if (!studioId) {
      // Says how to get one. The old text said "there is no name selector, by
      // design", which is justification the caller cannot act on - it explains
      // why the argument is required without saying where the value comes from.
      throw new ToolError(
        INVALID_ARGUMENT,
        "studio_id is required for action='stop'. Call action='list' with this " +
          "tool - its mesh rows carry one.",
      );
    }
    const resolved = await resolvePid(client, studioId);
    return okText(JSON.stringify({ action, ...inst.stopProcess(resolved) }, null, 2));
  }

  if (action === "launch") {
    const placePath = args["place_path"] as string | undefined;
    const exe = inst.studioExeSafe();
    if (!placePath) {
      // No default place and no sentinel. The agent chooses what to open; a
      // silent default would make it impossible to tell which place is up.
      throw new ToolError(
        INVALID_ARGUMENT,
        "place_path is required for action='launch'. " +
          "Run action='places' or action='make_place' to choose one.",
      );
    }
    if (!exe) {
      // LAUNCH_FAILED rather than a question. "is Studio installed?" asks the
      // caller something it has no power to answer, and the bare message
      // classified as UNKNOWN because the pattern table wants
      // "no robloxstudio executable" and this says "no Roblox Studio
      // executable". Says what to do instead.
      throw new ToolError(
        LAUNCH_FAILED,
        "no Roblox Studio executable found under the standard install paths. " +
          "Install Studio, or launch it yourself and use action='list'.",
      );
    }
    const pid = inst.launchViaFile(exe, ["--task", "EditFile", "--localPlaceFile", placePath]);
    return okText(
      JSON.stringify(
        {
          action,
          launched: true,
          pid,
          place_path: placePath,
          wait_seconds: inst.LAUNCH_WAIT,
          note:
            "Studio names its log ~1.5s after start but joins the mesh ~26s in, " +
            "measured on an already-warm place. Poll action=list rather than assuming.",
        },
        null,
        2,
      ),
    );
  }

  if (action === "make_place") {
    return okText(
      JSON.stringify(
        { action, made: false, error: "make_place is not implemented on the Node side yet" },
        null,
        2,
      ),
    );
  }

  throw new ToolError(
    INVALID_ARGUMENT,
    "unknown action " +
      describe(action) +
      ". Accepted: " +
      manageInstanceActions().join(", ") +
      ".",
    { tool: "extended_manage_instance", received: action, accepted: manageInstanceActions() },
  );
}

/**
 * The `action` values `extended_manage_instance` advertises.
 *
 * Read from the schema so the "Accepted:" list in the unknown-action error
 * cannot drift from what `tools/list` told the caller it may send. Mirrors
 * `_manage_instance_actions` in `python/src/roblox_studio_mcp/extended_server.py`;
 * the parity test checks the two advertise the same set.
 */
function manageInstanceActions(): string[] {
  const tool = EXTENDED_TOOLS.find((t) => t.name === "extended_manage_instance");
  const actions = (tool?.properties?.["action"] as { enum?: unknown } | undefined)?.enum;
  return Array.isArray(actions) ? [...(actions as string[])].sort() : [];
}

/**
 * Map a `studio_id` to a pid.
 *
 * The mesh row carries only `id` and `name`, so this joins on the name. With two
 * Studios open on one place both report `Place1` and the join is ambiguous.
 * Python resolves that with the log-based chain in `logid.ts`, which is ported
 * but not yet wired in here. The ambiguity is reported rather than guessed.
 */
async function resolvePid(client: MCPClient, studioId: string): Promise<number> {
  const raw = await (client as unknown as {
    callTool(name: string, args: Record<string, unknown>): Promise<unknown>;
  }).callTool("list_roblox_studios", {});
  const studios = Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
  if (studios.length === 0) {
    throw new Error("no Roblox Studio instances are connected");
  }
  if (!studios.some((s) => s["id"] === studioId)) {
    throw new Error(`requested studio_id is not connected: ${studioId}`);
  }
  const inst = await import("./extended/instance.js");
  const live = inst.listStudioProcesses(true).filter((p) => p.attached_to_mesh);
  if (live.length === 0) {
    throw new Error("place is not open: no attached Studio process found");
  }
  if (live.length === 1) {
    return live[0]!.pid;
  }
  throw new Error(
    `ambiguous: ${live.length} attached Studio processes are open, and the log-based ` +
      `identity chain in logid.ts that would tell them apart is not wired into ` +
      `instance.ts yet. Use the Python server for action=stop on a multi-Studio machine.`,
  );
}

async function callWaitFor(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { waitFor } = await import("./extended/waiting.js");
  const condition = requireStr(args, "condition");
  // `?? 30` rather than `|| 30`: a requested 0 must not become 30, because 0 is
  // falsy. The Python side had exactly that bug in watch_output's max_lines.
  const timeout = Number(args["timeout_seconds"] ?? 30.0);
  const datamodel = String(args["datamodel_type"] ?? "Edit");
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const verdict = await waitFor(studio, condition, timeout, { datamodelType: datamodel });
  return okText(JSON.stringify(verdict, null, 2));
}

async function callCapture(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const { capturePng } = await import("./extended/capture.js");
  const savePath = args["save_path"] as string | undefined;
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  const result = await capturePng(studio, savePath ? { savePath } : {});
  // snake_case on the wire, spelled out rather than spread.
  //
  // `CaptureResult` is camelCase internally (`rawBytes`, `savePath`) while Python's
  // result dict is snake_case, so spreading `...result` shipped `rawBytes` and
  // `savePath` to a caller that had been promised `raw_bytes` and `save_path` -
  // two servers returning the same tool's result under different key names. A
  // caller that reads `save_path` got `undefined` and no error, which is the
  // silent-wrong-answer shape. The parity gate does not catch it: it compares
  // the tool *surface*, not result payloads.
  return okText(
    JSON.stringify(
      {
        width: result.width,
        height: result.height,
        raw_bytes: result.rawBytes,
        png_bytes: result.pngBytes,
        mime: result.mime,
        lossless: result.lossless,
        captured_at: result.capturedAt,
        // Present only when a path was given, matching Python: `save_path` is
        // absent rather than null when there is nothing to report.
        ...(result.savePath ? { save_path: result.savePath } : {}),
        ...(result.pngBase64 ? { png_base64: result.pngBase64 } : {}),
        // Echo the target so a caller never re-derives which Studio answered.
        studio_id: studioId ?? studio.studioId,
      },
      null,
      2,
    ),
  );
}

async function callListStudios(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const registry = await import("./extended/registry.js");
  const selector = args["resolve"];
  if (selector !== null && typeof selector === "object") {
    const sel = selector as Record<string, unknown>;
    const result = registry.resolve({
      debugId: sel["debug_id"] as string | undefined,
      name: sel["name"] as string | undefined,
      placeId: sel["place_id"] as number | undefined,
    });
    return okText(JSON.stringify(result, null, 2));
  }
  const refresh = args["refresh"] !== false;
  if (refresh) {
    const result = await registry.listInstances(new RobloxStudio(client, null));
    return okText(JSON.stringify(result, null, 2));
  }
  const data = registry.loadAll();
  return okText(
    JSON.stringify(
      {
        version: data.version,
        registry_path: registry.registryPath(),
        count: Object.keys(data.instances).length,
        instances: Object.values(data.instances),
        note: "refresh=false, so values are as last recorded",
      },
      null,
      2,
    ),
  );
}

/**
 * Tools that only observe. Everything else is left unmarked, which a client reads
 * as mutating, so adding a new tool is safe by default rather than a way to
 * accidentally grant parallel execution.
 *
 * Mirrors Python's `_READONLY_TOOLS`. Two deliberate omissions, carried over so
 * the two sides agree:
 *
 * - `extended_wait_for` runs a caller-supplied Luau probe, so it can execute
 *   anything the probe executes. It reads in practice and is not marked, because
 *   "in practice" is not a guarantee.
 * - `extended_manage_instance` launches and stops Studios. Its `list` action does
 *   not, but a single hint cannot describe both.
 */
const READ_ONLY_TOOLS: ReadonlySet<string> = new Set([
  "extended_script_grep",
  "extended_script_search_and_read",
  "extended_watch_output",
  "extended_capture",
  "extended_skill",
  "extended_studio_identity",
  "extended_list_studios",
]);

for (const tool of EXTENDED_TOOLS) {
  if (READ_ONLY_TOOLS.has(tool.name)) {
    tool.readOnly = true;
  }
}

const _unknownReadOnly = [...READ_ONLY_TOOLS].filter(
  (name) => !EXTENDED_TOOLS.some((t) => t.name === name),
);
if (_unknownReadOnly.length > 0) {
  throw new Error(
    `read-only set names tools that do not exist: ${_unknownReadOnly.join(", ")}`,
  );
}

export const EXTENDED_HANDLERS: Record<string, (client: MCPClient, args: Record<string, unknown>) => Promise<ExtendedResult>> = {
  extended_write_script: callExtendedWrite,
  extended_update_script: callExtendedUpdate,
  extended_script_grep: callScriptGrep,
  extended_script_search_and_read: callScriptSearchRead,
  extended_insert_asset_from_file: callInsertAsset,
  extended_watch_output: callWatchOutput,
  extended_run_tests: callRunTests,
  extended_execute_luau_from_file: callExecuteFile,
  extended_list_studios: callListStudios,
  extended_studio_identity: callStudioIdentity,
  extended_capture: callCapture,
  extended_skill: callSkill,
  extended_breakpoints: callBreakpoints,
  extended_clear_breakpoints: callClearBreakpoints,
  extended_wait_for: callWaitFor,
  extended_manage_instance: callManageInstance,
};

// ---------------------------------------------------------------------------
// Argument validation
// ---------------------------------------------------------------------------

/**
 * Turn anything thrown into a JSON-RPC error that keeps a stable code.
 *
 * `classify` is called for everything that is not already a `ToolError`. That is
 * the safety net, not the mechanism: every argument-validation and targeting
 * failure now throws a `ToolError` with its code pinned at the raise site, so
 * the code does not depend on recognising prose. `classify` remains for the
 * faults that are genuinely somebody else's - the engine's refusals and the
 * targeting errors below it.
 *
 * Why that ordering matters: a code inferred from a message is a code that
 * moves when the message is reworded, and an agent branching on it then fails
 * silently. `parity/errors.json` pins the code for every raise site so a
 * reword cannot quietly reclassify one.
 *
 * `err.message`, never `String(err)`: `String()` on an Error prepends the class
 * name, so the agent was being told `"MCPToolError: No Roblox Studio instances
 * are connected."` - a class name it cannot act on, and which changes if the
 * class is renamed.
 */
export function toJsonRpcError(exc: unknown): Record<string, unknown> {
  const err = exc instanceof ToolError ? exc : classify(exc);
  return {
    code: -32000,
    message: err.message,
    data: { code: err.code, ...err.data },
  };
}

/** name -> declared property names, built from the same objects `tools/list` serves. */
const TOOL_PROPERTIES: Record<string, Set<string>> = Object.fromEntries(
  EXTENDED_TOOLS.map((t) => [t.name, new Set(Object.keys(t.properties))]),
);

/**
 * Refuse a call carrying a parameter the tool does not declare.
 *
 * **This is the single highest-value check in the server**, and it is here
 * because of what it prevents rather than what it reports.
 *
 * A silently-ignored parameter produces a *plausible wrong answer* rather than
 * an error, and this project has three separate instances of exactly that:
 *
 * - `screen_capture` accepted `format: "png"` and returned JPEG. Eight parameter
 *   names were tried before it was noticed, because none of them errored.
 * - **Node's own `extended_watch_output` had no `pattern`.** Passing it returned
 *   the whole console buffer with no error, which reads identically to "no
 *   breakpoint was hit". That gap is why this check exists.
 * - `max_lines: 0` became 200 through a falsy default.
 *
 * In each case the tool reported success. Mirrors the Python implementation; the
 * two must agree or the same call behaves differently per server.
 */
export function rejectUnknownArguments(name: string, args: Record<string, unknown>): void {
  const declared = TOOL_PROPERTIES[name];
  if (!declared || args === null || typeof args !== "object") return;

  const unknown = Object.keys(args).filter((key) => !declared.has(key));
  if (unknown.length === 0) return;

  const rendered = unknown
    .map((key) => {
      const suggestion = closest(key, declared);
      return suggestion ? `${key} (did you mean ${suggestion}?)` : key;
    })
    .join(", ");
  throw new ToolError(
    INVALID_ARGUMENT,
    `${name} does not accept ${rendered}. Accepted: ${
      [...declared].sort().join(", ") || "(none)"
    }`,
    // The same three keys Python attaches, in the same flat shape. Node
    // passed no `data` at all here, so a caller that read `data.unknown` to
    // decide what to fix got `undefined` on one server and the answer on the
    // other - a divergence that fails silently on exactly the branch a caller
    // writes to handle the error.
    { tool: name, unknown: unknown.sort(), accepted: [...declared].sort() },
  );
}

/**
 * The declared parameter nearest to `key`, if one is close enough to be worth
 * suggesting.
 *
 * The threshold is 0.4 rather than Python's difflib 0.5 because the metric
 * differs: this is a Dice coefficient over bigrams, where `line_number` against
 * `line` scores 0.4. The two cutoffs were chosen so **both implementations
 * accept and reject the same arguments**, which is the part that is contractual.
 * The suggestion *text* may differ when two candidates are near-tied, and
 * `node/tests/arguments.test.ts` asserts the accept/reject decision rather than
 * the wording.
 *
 * Below the threshold it returns nothing, because a wrong suggestion is worse
 * than none.
 */
function closest(key: string, declared: Set<string>): string | null {
  const candidates = [...declared].sort();
  let best: string | null = null;
  let bestScore = 0.4;
  for (const candidate of candidates) {
    const score = similarity(key, candidate);
    if (score > bestScore) {
      bestScore = score;
      best = candidate;
    }
  }
  return best;
}

/** Dice coefficient over bigrams. Small inputs, so the naive form is fine. */
function similarity(a: string, b: string): number {
  if (a === b) return 1;
  if (a.length < 2 || b.length < 2) return 0;
  const bigrams = new Map<string, number>();
  for (let i = 0; i < a.length - 1; i += 1) {
    const gram = a.slice(i, i + 2);
    bigrams.set(gram, (bigrams.get(gram) ?? 0) + 1);
  }
  let shared = 0;
  for (let i = 0; i < b.length - 1; i += 1) {
    const gram = b.slice(i, i + 2);
    const count = bigrams.get(gram) ?? 0;
    if (count > 0) {
      shared += 1;
      bigrams.set(gram, count - 1);
    }
  }
  return (2 * shared) / (a.length + b.length - 2);
}

// ---------------------------------------------------------------------------
// JSON-RPC relay loop
// ---------------------------------------------------------------------------

export interface ExtendedProxyClientLike {
  protocolVersion: string;
  capabilities: Record<string, unknown>;
  serverInfo: Record<string, unknown>;
  request(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
  close(): Promise<void>;
}

export async function handleExtendedMessage(
  client: MCPClient,
  message: Record<string, unknown>,
  send: SendFn = baseSend,
): Promise<void> {
  if (!("method" in message)) {
    return;
  }
  const method = message["method"];
  const hasId = "id" in message;
  const params = (message["params"] as Record<string, unknown>) ?? {};

  if (method === "initialize") {
    if (hasId) {
      send({
        jsonrpc: "2.0",
        id: message["id"],
        result: {
          protocolVersion: client.protocolVersion,
          capabilities: client.capabilities,
          serverInfo: client.serverInfo,
        },
      });
    }
    return;
  }

  if (method === "notifications/initialized" || method === "notifications/cancelled") {
    return;
  }

  if (method === "ping") {
    if (hasId) {
      send({ jsonrpc: "2.0", id: message["id"], result: {} });
    }
    return;
  }

  if (method === "tools/list") {
    if (!hasId) {
      return;
    }
    let base: Record<string, unknown>;
    try {
      base = await client.request("tools/list", params);
    } catch (exc) {
      send({ jsonrpc: "2.0", id: message["id"], error: { code: -32000, message: String(exc) } });
      return;
    }
    // Steer callers toward the extended tools: append a note to each relayed tool
    // that has a better equivalent, so the choice happens in the one place it is
    // actually made - the tool list, the only always-on surface. A skill is
    // opt-in, so a trap living only there has already been walked into by the
    // time anyone reads it. Mirrored into `parity/tools.json` and asserted by
    // both suites, because a drift here is a model using the worse tool forever
    // and nothing anywhere reports it.
    const baseTools = ((base["tools"] as unknown[]) ?? []).map((t) => {
      if (t === null || typeof t !== "object") return t;
      const row = t as Record<string, unknown>;
      const steer = STEERS[row["name"] as string];
      if (!steer) return t;
      // Appended, never replaced: the relayed description is Studio's and a
      // caller may be relying on it. Someone who wants the raw tool still gets
      // it, with the alternative named.
      return { ...row, description: `${String(row["description"] ?? "").trim()} ${steer}`.trim() };
    });
    const extendedDicts = EXTENDED_TOOLS.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
    }));
    send({
      jsonrpc: "2.0",
      id: message["id"],
      result: { tools: [...baseTools, ...extendedDicts] },
    });
    return;
  }

  if (method === "tools/call") {
    if (!hasId) {
      return;
    }
    const name = params["name"] as string | undefined;
    const args = (params["arguments"] as Record<string, unknown>) ?? {};

    if (name && name in EXTENDED_HANDLERS) {
      let result: ExtendedResult;
      // Reject unknown parameters *before* dispatch (request P0.2a).
      try {
        rejectUnknownArguments(name, args);
        result = await EXTENDED_HANDLERS[name](client, args);
      } catch (exc) {
        send({ jsonrpc: "2.0", id: message["id"], error: toJsonRpcError(exc) });
        return;
      }
      send({ jsonrpc: "2.0", id: message["id"], result });
      return;
    }

    if (name && EXTENDED_HANDLERS[name]) {
      let result: ExtendedResult;
      // Reject unknown parameters *before* dispatch (request P0.2a).
      try {
        rejectUnknownArguments(name, args);
        result = await EXTENDED_HANDLERS[name](client, args);
      } catch (exc) {
        send({ jsonrpc: "2.0", id: message["id"], error: toJsonRpcError(exc) });
        return;
      }
      send({ jsonrpc: "2.0", id: message["id"], result });
      return;
    }

    // A guard on a RELAYED call, not a rewritten one. Mirrors `_RELAY_GUARDS` in
    // the Python server; see that module for why "we cannot add parameters to a
    // relayed tool" was never a reason to accept a wrong target. The call is
    // forwarded untouched when it passes.
    const guard = name ? RELAY_GUARDS[name] : undefined;
    const relayNotes: string[] = [];
    if (guard) {
      try {
        await guard(client, args, relayNotes);
      } catch (exc) {
        send({ jsonrpc: "2.0", id: message["id"], error: toJsonRpcError(exc) });
        return;
      }
    }

    // Pass through to the underlying Studio MCP.
    let result: Record<string, unknown>;
    try {
      result = await client.request("tools/call", params);
    } catch (exc) {
      send({ jsonrpc: "2.0", id: message["id"], error: toJsonRpcError(exc) });
      return;
    }
    // A guard that could not run its check says so rather than staying silent -
    // "we verified" and "we could not check" must not look the same.
    if (relayNotes.length > 0) result = { ...result, proxy_note: relayNotes };
    send({ jsonrpc: "2.0", id: message["id"], result });
    return;
  }

  if (hasId) {
    send({
      jsonrpc: "2.0",
      id: message["id"],
      error: { code: -32601, message: `Method not found: ${method}` },
    });
  }
}

/** Run the extended stdio proxy until stdin closes. */
export async function serve(client?: MCPClient): Promise<void> {
  let owned = false;
  let active: MCPClient;
  if (client) {
    active = client;
  } else {
    active = new MCPClient(defaultCommand(), defaultArgs(), { shell: defaultShell() });
    await active.connect();
    owned = true;
  }

  try {
    const rl = createInterface({ input: process.stdin, terminal: false });
    try {
      for await (const line of rl) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }
        let message: unknown;
        try {
          message = JSON.parse(trimmed);
        } catch {
          continue;
        }
        if (typeof message === "object" && message !== null) {
          await handleExtendedMessage(active, message as Record<string, unknown>);
        }
      }
    } finally {
      rl.close();
    }
  } finally {
    if (owned) {
      await active.close();
    }
  }
}

function isMainModule(): boolean {
  const entry = process.argv[1] ?? "";
  return entry.endsWith("extendedServer.js");
}

if (isMainModule()) {
  serve().catch((err) => {
    console.error(err);
    process.exitCode = 1;
  });
}
