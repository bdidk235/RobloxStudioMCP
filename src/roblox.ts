/**
 * Convenience layer for the Roblox Studio MCP server.
 *
 * The Studio MCP proxy is launched by `cmd.exe /c %LOCALAPPDATA%\Roblox\mcp.bat`
 * and exposes a set of tools, almost all of which require a `studio_id`
 * identifying the target Studio instance (obtained from `list_roblox_studios`).
 * This module wraps {@link MCPClient} so that the `studio_id` is resolved once
 * and injected automatically into every tool call that needs it.
 */

import { DEFAULT_TIMEOUT, MCPClient } from "./client.js";
import { MCPToolError } from "./errors.js";
import { CallToolResult, Tool } from "./types.js";

// The Studio MCP proxy launcher on Windows. The `cd /d ... && .\mcp.bat` form
// is what Roblox's own configuration uses; it runs the proxy from the Roblox
// data directory, which the proxy relies on to locate the running Studio
// instance.
export const WINDOWS_COMMAND = "cmd.exe";
export const WINDOWS_ARGS: readonly string[] = ["/c", '"cd /d %LOCALAPPDATA%\\Roblox && .\\mcp.bat"'];

/**
 * Studio MCP proxy binary inside the macOS Studio app bundle (no shell
 * wrapper needed — it speaks stdio directly).
 * See https://create.roblox.com/docs/studio/mcp
 */
export const MACOS_COMMAND = "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP";

/** Launch settings for the Studio MCP proxy on a given platform. */
export interface PlatformDefaults {
  command: string;
  args: string[];
  /** Windows needs shell mode for its compound `cmd.exe` command; macOS spawns the binary directly. */
  shell: boolean;
}

/**
 * Launch settings for the Studio MCP proxy, selected by platform.
 * macOS runs the bundled `StudioMCP` binary directly; every other platform
 * keeps the Windows `cmd.exe /c …mcp.bat` default (pass explicit
 * `command`/`args`/`shell` to override on any platform).
 */
export function platformDefaults(platform: NodeJS.Platform = process.platform): PlatformDefaults {
  if (platform === "darwin") {
    return { command: MACOS_COMMAND, args: [], shell: false };
  }
  return { command: WINDOWS_COMMAND, args: [...WINDOWS_ARGS], shell: true };
}

/**
 * How long resolveStudioId() rides through a fresh proxy whose Studio uplink
 * is not usable yet before giving up. A new proxy answers list_roblox_studios
 * with "Unable to reach Roblox Studio" for a beat after its handshake.
 */
export const RESOLVE_TIMEOUT_MS = 10_000;
export const RESOLVE_INTERVAL_MS = 200;

// Matches the proxy's transient not-ready symptom (fresh proxy, uplink warming).
const NOT_READY_HINT = "unable to reach";

function isNotReadyError(exc: unknown): boolean {
  return exc instanceof MCPToolError && String(exc.message).toLowerCase().includes(NOT_READY_HINT);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface ResolveOptions {
  timeoutMs?: number;
  intervalMs?: number;
}

export interface RobloxStudioConnectOptions {
  studioId?: string | null;
  command?: string;
  args?: readonly string[];
  env?: Record<string, string>;
  timeout?: number;
  shell?: boolean;
  disabledTools?: Iterable<string>;
  /** When true (default), return the process-wide shared connection. */
  singleton?: boolean;
}

export interface ListToolsOptions {
  refresh?: boolean;
}

/** Minimal client surface needed by RobloxStudio (real MCPClient or a test double). */
export interface StudioClientLike {
  listTools(): Promise<Tool[]>;
  callTool(name: string, args?: Record<string, unknown>): Promise<CallToolResult>;
  readonly isConnected: boolean;
  close(): Promise<void>;
}

/**
 * A high-level client tuned for the Roblox Studio MCP server.
 *
 * @example
 * ```ts
 * import { RobloxStudio } from "roblox-studio-mcp-node";
 *
 * const studio = await RobloxStudio.connect();
 * try {
 *   console.log((await studio.listTools()).map((t) => t.name));
 *   console.log((await studio.call("execute_luau", { code: "return 1 + 1", datamodel_type: "Edit" })).text());
 * } finally {
 *   await studio.close();
 * }
 * ```
 */
export class RobloxStudio {
  protected studioIdValue: string | null;
  protected tools: Map<string, Tool> = new Map();
  protected isSingleton: boolean;

  constructor(
    readonly client: StudioClientLike,
    studioId: string | null = null,
    options: { isSingleton?: boolean } = {},
  ) {
    this.client = client;
    this.studioIdValue = studioId;
    this.isSingleton = options.isSingleton ?? false;
  }

  /**
   * Launch the Studio MCP proxy and connect to it.
   *
   * @param studioId The Studio instance to target. If omitted, it is resolved
   * lazily on the first tool call via `list_roblox_studios` (first wins).
   */
  static async connect(options: RobloxStudioConnectOptions = {}): Promise<RobloxStudio> {
    const {
      studioId = null,
      command = defaultCommand(),
      args = defaultArgs(),
      env,
      timeout = DEFAULT_TIMEOUT,
      shell = defaultShell(),
      disabledTools,
      singleton = true,
    } = options;
    if (singleton) {
      return getSingleton(studioId, { command, args, env, timeout, shell, disabledTools });
    }
    const client = new MCPClient(command, args, { env, timeout, shell, disabledTools });
    await client.connect();
    return new RobloxStudio(client, studioId ?? null);
  }

  /** The resolved `studio_id`, or `null` until first resolved. */
  get studioId(): string | null {
    return this.studioIdValue;
  }

  /** Point this client at a different Studio instance (public setter). */
  setStudioId(studioId: string | null): void {
    this.studioIdValue = studioId;
  }

  /** Forget cached `listTools` results so the next call re-fetches. */
  invalidateCache(): void {
    this.tools.clear();
  }

  /**
   * Close the connection to the Studio MCP proxy.
   *
   * If this instance is the process-wide singleton, the singleton is also
   * forgotten so a later `getSingleton` call starts fresh.
   */
  async close(): Promise<void> {
    await this.client.close();
    if (singletonInstance === this) {
      singletonInstance = null;
    }
  }

  async [Symbol.asyncDispose](): Promise<void> {
    // Don't close the shared process when used as
    // `await using studio = await RobloxStudio.connect()` (singleton default).
    // Explicitly call `await studio.close()` or `closeSingleton()`.
    if (this.isSingleton) {
      return;
    }
    await this.close();
  }

  /** Return all tools exposed by the Studio MCP server. */
  async listTools(options: ListToolsOptions = {}): Promise<Tool[]> {
    const { refresh = false } = options;
    if (refresh || this.tools.size === 0) {
      const tools = await this.client.listTools();
      this.tools = new Map(tools.map((t) => [t.name, t]));
    }
    return [...this.tools.values()];
  }

  /** Return a single tool by name (fetching the list if needed). */
  async getTool(name: string): Promise<Tool> {
    if (this.tools.size === 0) {
      await this.listTools();
    }
    const tool = this.tools.get(name);
    if (!tool) {
      throw new MCPToolError(`Unknown tool ${JSON.stringify(name)}. Available: ${JSON.stringify([...this.tools.keys()].sort())}`);
    }
    return tool;
  }

  /** Return the connected Studio instances as a list of dicts. */
  async listStudios(): Promise<Record<string, unknown>[]> {
    const result = await this.client.callTool("list_roblox_studios", {});
    const data = result.json() as unknown;
    if (Array.isArray(data)) {
      return data as Record<string, unknown>[];
    }
    if (data !== null && typeof data === "object") {
      for (const key of ["studios", "instances", "data", "result"]) {
        const value = (data as Record<string, unknown>)[key];
        if (Array.isArray(value)) {
          return value as Record<string, unknown>[];
        }
      }
    }
    return [];
  }

  /**
   * Return the `studio_id` to use, resolving it lazily if needed.
   *
   * A fresh proxy needs a moment after its handshake before its Studio uplink
   * is usable; until then `list_roblox_studios` fails with "Unable to reach
   * Roblox Studio". That specific transient symptom is retried until
   * `timeoutMs` has elapsed. Every other error — including a genuinely empty
   * instance list — still throws immediately.
   */
  async resolveStudioId(options: ResolveOptions = {}): Promise<string> {
    const { timeoutMs = RESOLVE_TIMEOUT_MS, intervalMs = RESOLVE_INTERVAL_MS } = options;
    if (this.studioIdValue) {
      return this.studioIdValue;
    }
    const deadline = Date.now() + Math.max(0, timeoutMs);
    let studios: Record<string, unknown>[];
    for (;;) {
      try {
        studios = await this.listStudios();
      } catch (exc) {
        if (!isNotReadyError(exc) || Date.now() >= deadline) {
          throw exc;
        }
        await delay(Math.max(0, Math.min(intervalMs, deadline - Date.now())));
        continue;
      }
      break;
    }
    if (studios.length === 0) {
      throw new MCPToolError("No Roblox Studio instances are connected. Open Studio and enable the MCP plugin, then retry.");
    }
    const first = studios[0];
    for (const key of ["id", "studio_id", "studioId"]) {
      if (first[key]) {
        this.studioIdValue = String(first[key]);
        return this.studioIdValue;
      }
    }
    throw new MCPToolError(`Could not determine studio_id from list_roblox_studios result: ${JSON.stringify(first)}`);
  }

  /**
   * Call a Studio MCP tool, injecting `studio_id` when required.
   *
   * `studio_id` is only added when the tool's input schema declares it and
   * the caller did not already supply one.
   */
  async call(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
    const merged: Record<string, unknown> = { ...args };
    if (!("studio_id" in merged)) {
      let tool: Tool | null = null;
      try {
        tool = await this.getTool(name);
      } catch {
        tool = null;
      }
      if (tool === null || tool.hasParameter("studio_id")) {
        merged["studio_id"] = await this.resolveStudioId();
      }
    }
    return this.client.callTool(name, merged);
  }

  /** Run Luau code inside Studio and return the result. */
  async executeLuau(code: string, datamodelType = "Edit", extra: Record<string, unknown> = {}): Promise<CallToolResult> {
    return this.call("execute_luau", { code, datamodel_type: datamodelType, ...extra });
  }

  /** Return Studio's play state and available datamodel types. */
  async getStudioState(): Promise<CallToolResult> {
    return this.call("get_studio_state", {});
  }

  /** Start play testing. */
  async startPlay(): Promise<CallToolResult> {
    return this.call("start_stop_play", { is_start: true });
  }

  /** Stop play testing and return to edit mode. */
  async stopPlay(): Promise<CallToolResult> {
    return this.call("start_stop_play", { is_start: false });
  }

  /** Read a script from the DataModel (e.g. `game.ServerScriptService.MyScript`). */
  async scriptRead(targetFile: string, extra: Record<string, unknown> = {}): Promise<CallToolResult> {
    return this.call("script_read", { target_file: targetFile, ...extra });
  }

  /** Inspect an instance's properties, attributes and children. */
  async inspectInstance(path: string): Promise<CallToolResult> {
    return this.call("inspect_instance", { path });
  }

  /** Explore the DataModel hierarchy. */
  async searchGameTree(kwargs: Record<string, unknown> = {}): Promise<CallToolResult> {
    return this.call("search_game_tree", kwargs);
  }

  /** Capture the current Studio viewport. */
  async screenCapture(captureId = "ScreenCapture_1", extra: Record<string, unknown> = {}): Promise<CallToolResult> {
    return this.call("screen_capture", { capture_id: captureId, ...extra });
  }
}

/** The command used to launch the Studio MCP proxy (platform-aware). */
export function defaultCommand(): string {
  return platformDefaults().command;
}

/** Whether the proxy launch needs shell mode (platform-aware). */
export function defaultShell(): boolean {
  return platformDefaults().shell;
}

/**
 * The arguments used to launch the Studio MCP proxy (platform-aware).
 *
 * `%LOCALAPPDATA%` is intentionally left unexpanded: the proxy is launched in
 * shell mode on Windows, so `cmd.exe` expands it itself. macOS takes no args.
 */
export function defaultArgs(): string[] {
  return platformDefaults().args;
}

// ---------------------------------------------------------------------------
// Process-wide singleton connection
// ---------------------------------------------------------------------------

let singletonInstance: RobloxStudio | null = null;
let singletonPromise: Promise<RobloxStudio> | null = null;

export interface SingletonOptions {
  command?: string;
  args?: readonly string[];
  env?: Record<string, string>;
  timeout?: number;
  shell?: boolean;
  disabledTools?: Iterable<string>;
}

/**
 * Return a process-wide shared {@link RobloxStudio} connection.
 *
 * The first call creates and connects a client; every subsequent call returns
 * the same instance (reconnecting only if the underlying connection dropped).
 */
export async function getSingleton(
  studioId: string | null = null,
  options: SingletonOptions = {},
): Promise<RobloxStudio> {
  if (singletonInstance !== null && singletonInstance.client.isConnected) {
    if (studioId && singletonInstance.studioId !== studioId) {
      // Caller wants a different Studio target: re-point the shared
      // instance instead of spawning a second proxy process.
      singletonInstance.setStudioId(studioId);
    }
    return singletonInstance;
  }
  if (singletonPromise !== null) {
    const existing = await singletonPromise;
    if (studioId && existing.studioId !== studioId) {
      existing.setStudioId(studioId);
    }
    return existing;
  }
  singletonPromise = (async () => {
    const client = new MCPClient(options.command ?? defaultCommand(), options.args ?? defaultArgs(), {
      env: options.env,
      timeout: options.timeout ?? DEFAULT_TIMEOUT,
      shell: options.shell ?? defaultShell(),
      disabledTools: options.disabledTools,
    });
    await client.connect();
    singletonInstance = new RobloxStudio(client, studioId ?? null, { isSingleton: true });
    return singletonInstance;
  })();
  try {
    return await singletonPromise;
  } finally {
    singletonPromise = null;
  }
}

/** Close and forget the shared connection created by {@link getSingleton}. */
export async function closeSingleton(): Promise<void> {
  if (singletonInstance !== null) {
    const inst = singletonInstance;
    singletonInstance = null;
    await inst.close();
  }
}

/** Reset singleton state (test-only). */
export function __resetSingletonForTests(): void {
  singletonInstance = null;
  singletonPromise = null;
}
