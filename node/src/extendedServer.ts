/**
 * Extended stdio MCP server that wraps the Studio MCP with write/update tools.
 *
 * A thin layer over the standard Studio MCP proxy that adds convenience
 * tools on top of the raw Studio MCP tools (see EXTENDED_TOOLS):
 *
 * - `extended_write_like_multi_edit` — full-body script replacement
 * - `extended_update_like_multi_edit` — batch edits with graceful skipping
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
import { writeLikeMultiEdit } from "./extended/writer.js";
import { updateLikeMultiEdit, type UpdateResult } from "./extended/updater.js";

// ---------------------------------------------------------------------------
// Extended tool definitions (for tools/list)
// ---------------------------------------------------------------------------

export const EXTENDED_TOOLS: Tool[] = [
  new Tool(
    "extended_write_like_multi_edit",
    "RECOMMENDED over raw multi_edit for full-file writes. Replaces a game-tree script's entire body with Claude Code Write semantics: reads the current source first, writes atomically, and returns \"unchanged\" without writing when the content is already identical. Game-tree paths only (must start with \"game.\"). Set create_if_missing:true to create a missing script (otherwise a missing script is an error — new files are never created implicitly). Returns a status string \"wrote\", \"unchanged\", or \"created\".",
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
    "extended_update_like_multi_edit",
    "RECOMMENDED over raw multi_edit for targeted edits. Applies a batch of exact string-replacement edits to a game-tree script with Claude Code Edit semantics: reads the current source first, applies edits in order (each operating on the result of the previous edit), and requires each old_string to match exactly once — like Edit, an old_string that is missing fails with an old_string-not-found error and one matching multiple regions fails as ambiguous unless that edit sets replace_all:true (like Edit's replaceAll). By default missing/ambiguous/no-op edits are skipped with warnings and everything else is applied; set skip_missing:false or skip_no_ops:false for strict Edit-like failure. Returns a structured summary of what was updated, skipped, and any warnings.",
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
    "extended_script_search_and_read",
    "Search for scripts under a DataModel path, then batch-read their sources. Returns a JSON array of {path, source, name, line_count, truncated} entries. Sources are truncated to max_chars_per_source (default 2000) with truncated:true; pass 0 for full sources.",
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
    "Insert a local file into the game tree. file_type='image': uploads via store_image then insert_asset. file_type='script': reads the file and creates a Script/LocalScript/ModuleScript at parent_path.asset_name using writeLikeMultiEdit.",
    {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Local file path." },
        file_type: {
          type: "string",
          enum: ["image", "script"],
          default: "script",
          description: "Type of file: 'image' (png/jpg/jpeg) or 'script' (luau/lua).",
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
    "Poll the Studio console and return only new lines since the last call. Useful for live-tailing output during play testing.",
    {
      type: "object",
      properties: {
        studio_id: { type: "string", description: "Optional explicit studio_id to target." },
      },
    },
  ),
  new Tool(
    "extended_run_tests",
    "Run play testing and collect console output as a test summary. Returns {passed, console_lines, errors}.",
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
    "extended_execute_luau_from_file",
    "Execute Luau source read from a local file. Same as execute_luau, but the code comes from a .luau/.lua file on disk instead of an inline string — useful for long scripts kept under version control. Returns the execution result.",
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
    throw new Error(`Missing required string argument: ${JSON.stringify(field)}.`);
  }
  return value;
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
    throw new Error("Missing required string argument: 'content'.");
  }
  const className = (args["className"] as string | undefined) ?? null;
  const createIfMissing = Boolean(args["create_if_missing"] ?? false);
  const studioId = (args["studio_id"] as string | undefined) ?? null;

  const studio = new RobloxStudio(client, studioId);
  const status = await writeLikeMultiEdit(studio, targetPath, content, {
    className,
    createIfMissing,
  });
  return okText(status);
}

async function callExtendedUpdate(client: MCPClient, args: Record<string, unknown>): Promise<ExtendedResult> {
  const targetPath = requireStr(args, "target_path");
  const editsRaw = args["edits"];
  if (!Array.isArray(editsRaw) || editsRaw.length === 0) {
    throw new Error("Missing required argument: 'edits' must be a non-empty list.");
  }
  const skipMissing = Boolean(args["skip_missing"] ?? true);
  const skipNoOps = Boolean(args["skip_no_ops"] ?? true);
  const studioId = (args["studio_id"] as string | undefined) ?? null;

  const edits: Array<{ old_string: string; new_string: string; replace_all?: boolean }> = (
    editsRaw as unknown[]
  ).map((e) => {
    if (typeof e !== "object" || e === null) {
      throw new Error("Each edit must be {old_string, new_string}.");
    }
    const d = e as Record<string, unknown>;
    const oldString = (d["old_string"] ?? d["oldString"]) as unknown;
    const newString = (d["new_string"] ?? d["newString"]) as unknown;
    if (typeof oldString !== "string" || oldString.length === 0) {
      throw new Error("Each edit must have a non-empty string {old_string}.");
    }
    if (typeof newString !== "string") {
      throw new Error("Each edit must have a string {new_string}.");
    }
    const replaceAll = Boolean(d["replace_all"] ?? d["replaceAll"] ?? false);
    return { old_string: oldString, new_string: newString, replace_all: replaceAll };
  });

  const studio = new RobloxStudio(client, studioId);
  const result: UpdateResult = await updateLikeMultiEdit(studio, targetPath, edits, {
    skipMissing,
    skipNoOps,
  });

  let text = String(result);
  if (result.warnings.length > 0) {
    text += "\n" + result.warnings.map((w) => `  WARNING: ${w}`).join("\n");
  }
  return okText(text);
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
  const studioId = (args["studio_id"] as string | undefined) ?? null;
  const studio = new RobloxStudio(client, studioId);
  // Persistent per-studio state so consecutive calls return only new lines.
  const result = await watchOutput(studio, getWatchState(studioId ?? "default"));
  return okText(JSON.stringify(result, null, 2));
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

export const EXTENDED_HANDLERS: Record<string, (client: MCPClient, args: Record<string, unknown>) => Promise<ExtendedResult>> = {
  extended_write_like_multi_edit: callExtendedWrite,
  extended_update_like_multi_edit: callExtendedUpdate,
  extended_script_search_and_read: callScriptSearchRead,
  extended_insert_asset_from_file: callInsertAsset,
  extended_watch_output: callWatchOutput,
  extended_run_tests: callRunTests,
  extended_execute_luau_from_file: callExecuteFile,
};

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
    // Steer callers toward the extended tools: annotate the raw multi_edit
    // so models prefer extended_write_like_multi_edit (full-file writes) and
    // extended_update_like_multi_edit (targeted edits).
    const baseTools = ((base["tools"] as unknown[]) ?? []).map((t) =>
      t !== null && typeof t === "object" && (t as Record<string, unknown>)["name"] === "multi_edit"
        ? {
            ...(t as Record<string, unknown>),
            description: `${(t as Record<string, unknown>)["description"] ?? "Apply string-replacement edits to a script."} NOTE: prefer extended_write_like_multi_edit for full-file replacement and extended_update_like_multi_edit for targeted string replacements — they read first, enforce exact/unique matching like Claude Code Edit/Write, and report what changed.`,
          }
        : t,
    );
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
      try {
        result = await EXTENDED_HANDLERS[name](client, args);
      } catch (exc) {
        send({ jsonrpc: "2.0", id: message["id"], error: { code: -32000, message: String(exc) } });
        return;
      }
      send({ jsonrpc: "2.0", id: message["id"], result });
      return;
    }

    // Pass through to the underlying Studio MCP.
    let result: Record<string, unknown>;
    try {
      result = await client.request("tools/call", params);
    } catch (exc) {
      send({ jsonrpc: "2.0", id: message["id"], error: { code: -32000, message: String(exc) } });
      return;
    }
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
