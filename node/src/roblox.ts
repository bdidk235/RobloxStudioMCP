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

/**
 * Keys under which `list_roblox_studios` has been observed to carry its array.
 */
const STUDIO_LIST_KEYS = ["studios", "instances", "data", "result"] as const;

/**
 * The proxy's symptom for a `studio_id` naming a Studio it can no longer
 * reach: the instance closed, or its place unloaded. Distinct from the
 * not-ready hint above, which means the whole uplink is still warming.
 */
const STALE_ID_HINTS = ["is not connected", "place is not open"] as const;

function isNotReadyError(exc: unknown): boolean {
  return exc instanceof MCPToolError && String(exc.message).toLowerCase().includes(NOT_READY_HINT);
}

function isStaleStudioIdError(exc: unknown): boolean {
  if (!(exc instanceof MCPToolError)) return false;
  const message = String(exc.message).toLowerCase();
  return STALE_ID_HINTS.some((hint) => message.includes(hint));
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
  * import { RobloxStudio } from "roblox-studio-mcp";
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
   * on each tool call that needs it via `list_roblox_studios`, and only
   * accepted when exactly one Studio is connected. With more than one
   * connected the call throws rather than guessing; pass `studioId`
   * explicitly, or per call, to choose.
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

  /**
   * The explicitly configured `studio_id`, or `null`.
   *
   * An implicitly resolved id is never stored here, so this stays `null`
   * unless the caller pinned a Studio via `connect()` or `setStudioId()`.
   */
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

  /**
   * Return the connected Studio instances as a list of dicts.
   *
   * An empty array means the proxy really reported no instances. A payload
   * shape this client does not recognise throws instead, because the two are
   * different faults: collapsing them reports schema drift as "no Studio is
   * connected", which sends the caller to check the MCP toggle when the real
   * problem is on this side of the wire.
   */
  async listStudios(): Promise<Record<string, unknown>[]> {
    const result = await this.client.callTool("list_roblox_studios", {});
    const data = result.json() as unknown;
    if (Array.isArray(data)) {
      return data as Record<string, unknown>[];
    }
    if (data !== null && typeof data === "object") {
      const record = data as Record<string, unknown>;
      for (const key of STUDIO_LIST_KEYS) {
        if (key in record) {
          const value = record[key];
          if (Array.isArray(value)) {
            return value as Record<string, unknown>[];
          }
          throw new MCPToolError(
            `list_roblox_studios returned ${JSON.stringify(key)} as ${typeof value}, ` +
              `expected an array. Keys present: ${JSON.stringify(Object.keys(record).sort())}.`,
          );
        }
      }
      throw new MCPToolError(
        "list_roblox_studios returned an unrecognised shape: a dict with keys " +
          `${JSON.stringify(Object.keys(record).sort())} and none of ` +
          `${JSON.stringify(STUDIO_LIST_KEYS)}. This client needs updating; the ` +
          `response was ${JSON.stringify(data)}`,
      );
    }
    throw new MCPToolError(
      "list_roblox_studios returned an unrecognised shape: " +
        `${data === null ? "null" : typeof data}, expected an array or a dict. ` +
        `The response was ${JSON.stringify(data)}`,
    );
  }

  /**
   * Return the `studio_id` to use.
   *
   * An id configured by the caller (`connect({ studioId })` or
   * `setStudioId()`) is returned as-is and is never re-validated.
   *
   * Otherwise the id is resolved from `list_roblox_studios` on **every** call.
   * It is accepted only when exactly one Studio is connected: zero throws,
   * and more than one throws too, because list order is the proxy mesh's and
   * carries no intent — a silently chosen Studio is how probes end up reading
   * the wrong place. Pin the instance explicitly to disambiguate. Resolving
   * every time is deliberate: a cached id would keep being used after a second
   * Studio opened or the first restarted, which is exactly the ambiguity this
   * refuses.
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
    if (studios.length > 1) {
      const candidates = studios.map((s) => [s["name"], s["id"]]);
      throw new MCPToolError(
        `${studios.length} Roblox Studio instances are connected, so no studio_id can be inferred: ` +
          `${JSON.stringify(candidates)}. Pass studioId to connect() (or per call) to pick one.`,
      );
    }
    const only = studios[0]!;
    for (const key of ["id", "studio_id", "studioId"]) {
      if (only[key]) {
        return String(only[key]);
      }
    }
    throw new MCPToolError(`Could not determine studio_id from list_roblox_studios result: ${JSON.stringify(only)}`);
  }

  /**
   * Call a Studio MCP tool, injecting `studio_id` when required.
   *
   * `studio_id` is only added when the tool's input schema declares it and
   * the caller did not already supply one.
   *
   * A pinned `studio_id` that the proxy can no longer reach is reported with
   * what *is* currently connected, and is not silently swapped for a different
   * Studio: a pin is the caller's explicit choice, so replacing it would
   * reintroduce the guessing this layer exists to avoid. Unpin with
   * `setStudioId(null)` to fall back to inference.
   */
  async call(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
    const merged: Record<string, unknown> = { ...args };
    let usedPinned = false;
    if (!("studio_id" in merged)) {
      let tool: Tool | null = null;
      try {
        tool = await this.getTool(name);
      } catch {
        tool = null;
      }
      if (tool === null || tool.hasParameter("studio_id")) {
        usedPinned = this.studioIdValue !== null;
        merged["studio_id"] = await this.resolveStudioId();
      }
    }
    try {
      return await this.client.callTool(name, merged);
    } catch (exc) {
      if (!usedPinned || !isStaleStudioIdError(exc)) {
        throw exc;
      }
      throw new MCPToolError(
        `The pinned studio_id ${JSON.stringify(merged["studio_id"])} is no longer connected: ` +
          `${(exc as Error).message} Studio instance ids change every time Studio restarts, ` +
          `so a pin does not survive one. Re-pin with setStudioId() using a current id from ` +
          `listStudios(), or setStudioId(null) to fall back to inferring from whichever single ` +
          `Studio is open.`,
      );
    }
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
