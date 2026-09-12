/**
 * A minimal, dependency-free MCP client over the stdio transport.
 *
 * The Model Context Protocol speaks JSON-RPC 2.0 over stdin/stdout, with one
 * newline-delimited JSON message per line. This module implements just enough
 * of that protocol to be useful: connect + initialize handshake, list tools,
 * and call tools. Server-to-client requests are answered with a
 * `Method not found` error so the server never blocks waiting on us.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { createInterface, type Interface as ReadlineInterface } from "node:readline";
import { VERSION } from "./version.js";
import { JSONRPCError, MCPConnectionError, MCPToolError } from "./errors.js";
import { CallToolResult, Tool } from "./types.js";

export const DEFAULT_PROTOCOL_VERSION = "2024-11-05";
export const DEFAULT_TIMEOUT = 120_000; // ms (Python: 120.0s)

export interface MCPClientOptions {
  env?: Record<string, string>;
  cwd?: string;
  /** Per-request timeout in milliseconds. Default 120_000. */
  timeout?: number;
  protocolVersion?: string;
  /** Expand %VAR%/$VAR references in command/args before launch. Default true. */
  expandEnv?: boolean;
  shell?: boolean;
  disabledTools?: Iterable<string>;
}

interface PendingEntry {
  resolve: (value: Record<string, unknown>) => void;
  reject: (err: unknown) => void;
  timer: NodeJS.Timeout;
}

/**
 * Expand `%NAME%` (Windows) and `$NAME` / `${NAME}` (POSIX) references
 * using `process.env`, mirroring Python's `os.path.expandvars`.
 */
export function expandEnvVars(value: string): string {
  // %NAME% first (cmd.exe style)
  let out = value.replace(/%([^%]+)%/g, (_m, name: string) => process.env[name] ?? `%${name}%`);
  // ${NAME} and $NAME
  out = out.replace(/\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, braced: string, plain: string) => {
    const name = braced ?? plain;
    return process.env[name] ?? m;
  });
  return out;
}

/** An async client for a single stdio MCP server. */
export class MCPClient {
  timeout: number;
  protocolVersion: string;
  serverInfo: Record<string, unknown> = {};
  capabilities: Record<string, unknown> = {};

  private shell: boolean;
  private argv: string[] | null;
  private shellCmd: string | null;
  private env: NodeJS.ProcessEnv;
  private cwd: string | undefined;
  private disabledToolsSet: Set<string>;

  private proc: ChildProcess | null = null;
  private stdoutRl: ReadlineInterface | null = null;
  private stderrRl: ReadlineInterface | null = null;
  private pending = new Map<number, PendingEntry>();
  private sendChain: Promise<void> = Promise.resolve();
  private nextId = 0;
  private initialized = false;
  private stderrLinesBuf: string[] = [];
  private closed = false;
  private exitPromise: Promise<number | null> | null = null;

  /**
   * @param command The executable to launch (e.g. `"cmd.exe"`, `"npx"`, `"node"`).
   * @param args Arguments passed to `command`.
   */
  constructor(command: string, args: readonly string[] = [], options: MCPClientOptions = {}) {
    const { env, cwd, timeout = DEFAULT_TIMEOUT, protocolVersion = DEFAULT_PROTOCOL_VERSION, expandEnv = true, shell = false, disabledTools } = options;
    this.shell = shell;
    if (shell) {
      const parts = [command, ...args];
      this.shellCmd = parts.join(" ");
      this.argv = null;
    } else {
      let argv = [command, ...args];
      if (expandEnv) {
        argv = argv.map((a) => expandEnvVars(a));
      }
      this.argv = argv;
      this.shellCmd = null;
    }
    this.env = { ...process.env, ...(env ?? {}) };
    this.cwd = cwd;
    this.timeout = timeout;
    this.protocolVersion = protocolVersion;
    this.disabledToolsSet = new Set(disabledTools ?? []);
  }

  /** Spawn the server process and perform the initialize handshake. */
  async connect(): Promise<this> {
    if (this.proc !== null) {
      return this;
    }
    this.closed = false;

    let proc: ChildProcess;
    try {
      if (this.shell) {
        proc = spawn(this.shellCmd as string, {
          stdio: ["pipe", "pipe", "pipe"],
          env: this.env,
          cwd: this.cwd,
          shell: true,
          windowsHide: true,
        });
      } else {
        const [cmd, ...rest] = this.argv as string[];
        proc = spawn(cmd, rest, {
          stdio: ["pipe", "pipe", "pipe"],
          env: this.env,
          cwd: this.cwd,
          shell: false,
          windowsHide: true,
        });
      }
    } catch (exc) {
      const target = this.argv?.[0] ?? this.shellCmd;
      throw new MCPConnectionError(
        `Could not launch MCP server: ${JSON.stringify(target)} not found. ` +
          `Full command: ${this.shell ? this.shellCmd : (this.argv ?? []).join(" ")}`,
        { cause: exc },
      );
    }
    this.proc = proc;

    this.exitPromise = new Promise<number | null>((resolve) => {
      proc.on("exit", (code) => resolve(code));
      proc.on("error", () => resolve(null));
    });

    // Surface spawn failures (e.g. ENOENT) as connection errors instead of
    // hanging until the initialize timeout.
    const spawnError = new Promise<never>((_resolve, reject) => {
      proc.once("error", (err) => {
        const target = this.argv?.[0] ?? this.shellCmd;
        reject(
          new MCPConnectionError(
            `Could not launch MCP server: ${JSON.stringify(target)} failed to start (${(err as Error).message}). ` +
              `Full command: ${this.shell ? this.shellCmd : (this.argv ?? []).join(" ")}`,
            { cause: err },
          ),
        );
      });
    });

    if (!proc.stdout || !proc.stdin || !proc.stderr) {
      throw new MCPConnectionError("Failed to open stdio pipes to MCP server.");
    }

    this.stdoutRl = createInterface({ input: proc.stdout });
    this.stdoutRl.on("line", (line) => void this.onStdoutLine(line));
    this.stderrRl = createInterface({ input: proc.stderr });
    this.stderrRl.on("line", (line) => {
      this.stderrLinesBuf.push(line);
      if (this.stderrLinesBuf.length > 1000) {
        this.stderrLinesBuf.splice(0, this.stderrLinesBuf.length - 1000);
      }
    });

    // If the process exits immediately, fail pending requests.
    proc.on("exit", () => {
      this.failAllPending(new MCPConnectionError("MCP server connection closed unexpectedly"));
    });

    const result = (await Promise.race([
      this.request("initialize", {
        protocolVersion: this.protocolVersion,
        capabilities: {},
        clientInfo: { name: "roblox-studio-mcp-node", version: VERSION },
      }),
      spawnError,
    ])) as Record<string, unknown>;
    this.serverInfo = (result["serverInfo"] as Record<string, unknown>) ?? {};
    this.capabilities = (result["capabilities"] as Record<string, unknown>) ?? {};
    // Adopt the server's negotiated protocol version if it sent one.
    if (typeof result["protocolVersion"] === "string") {
      this.protocolVersion = result["protocolVersion"] as string;
    }

    await this.notify("notifications/initialized", {});
    this.initialized = true;
    return this;
  }

  /** Terminate the server process and clean up background tasks. */
  async close(): Promise<void> {
    const proc = this.proc;
    if (proc === null) {
      return;
    }
    this.closed = true;

    // Close our write end first so the server observes EOF and can exit.
    try {
      proc.stdin?.end();
    } catch {
      // ignore — the pipe may already be gone
    }

    if (proc.exitCode === null && proc.signalCode === null) {
      try {
        proc.kill();
      } catch {
        // already gone
      }
      try {
        await Promise.race([
          this.exitPromise,
          new Promise((resolve) => setTimeout(resolve, 5000)),
        ]);
      } catch {
        // ignore
      }
      if (proc.exitCode === null) {
        try {
          proc.kill("SIGKILL");
        } catch {
          // ignore
        }
        await this.exitPromise?.catch(() => null);
      }
    }

    this.stdoutRl?.close();
    this.stderrRl?.close();
    this.stdoutRl = null;
    this.stderrRl = null;
    this.proc = null;
    this.initialized = false;
  }

  /** Wait until the child process has fully exited. */
  async waitClosed(): Promise<void> {
    if (this.proc !== null && this.exitPromise !== null) {
      await this.exitPromise;
    }
  }

  async [Symbol.asyncDispose](): Promise<void> {
    await this.close();
  }

  /** Return the tools advertised by the server (disabled tools omitted). */
  async listTools(): Promise<Tool[]> {
    const result = await this.request("tools/list", {});
    const raw = (result["tools"] as Record<string, unknown>[] | undefined) ?? [];
    const tools = raw.map((t) =>
      Tool.fromDict({
        name: t["name"] as string,
        description: t["description"] as string,
        inputSchema: t["inputSchema"] as Record<string, unknown>,
      }),
    );
    return tools.filter((t) => !this.disabledToolsSet.has(t.name));
  }

  /**
   * Call a tool by name and return its result.
   * @throws {MCPToolError} If the tool is disabled via `disabledTools`.
   */
  async callTool(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
    if (this.disabledToolsSet.has(name)) {
      throw new MCPToolError(`Tool ${JSON.stringify(name)} is disabled.`);
    }
    const result = await this.request("tools/call", { name, arguments: args });
    const parsed = CallToolResult.fromDict({
      content: result["content"] as CallToolResult["content"],
      isError: result["isError"] as boolean,
    });
    if (parsed.isError) {
      throw new MCPToolError(`Tool ${JSON.stringify(name)} reported an error: ${parsed.text()}`);
    }
    return parsed;
  }

  /**
   * Send a raw JSON-RPC request and return the `result` dict.
   *
   * Unlike `listTools` and `callTool`, this does not parse, filter, or raise
   * on tool errors — it returns the server's raw response so callers (such
   * as the stdio proxy) can forward it verbatim.
   */
  async request(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.requestInternal(method, params);
  }

  private requestInternal(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.requireConnected();
    const requestId = this.newId();
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        reject(new MCPConnectionError(`Timed out waiting for ${JSON.stringify(method)} after ${this.timeout}ms`));
      }, this.timeout);
      // Prevent a long timeout from keeping the process alive on its own.
      (timer as unknown as { unref?: () => void }).unref?.();
      this.pending.set(requestId, { resolve, reject, timer });
      const message: Record<string, unknown> = { jsonrpc: "2.0", id: requestId, method };
      if (params !== undefined) {
        message["params"] = params;
      }
      void this.send(message).catch((err) => {
        const entry = this.pending.get(requestId);
        if (entry) {
          clearTimeout(entry.timer);
          this.pending.delete(requestId);
          entry.reject(err);
        }
      });
    });
  }

  private async notify(method: string, params?: Record<string, unknown>): Promise<void> {
    this.requireConnected();
    const message: Record<string, unknown> = { jsonrpc: "2.0", method };
    if (params !== undefined) {
      message["params"] = params;
    }
    await this.send(message);
  }

  private send(message: Record<string, unknown>): Promise<void> {
    const proc = this.requireConnected();
    const line = JSON.stringify(message) + "\n";
    // Serialize writes so lines never interleave.
    const task = this.sendChain.then(
      () =>
        new Promise<void>((resolve, reject) => {
          if (!proc.stdin || proc.stdin.destroyed) {
            reject(new MCPConnectionError("Not connected. Call `await client.connect()` first."));
            return;
          }
          proc.stdin.write(line, "utf-8", (err) => {
            if (err) reject(err);
            else resolve();
          });
        }),
    );
    // Keep the chain alive even if one write fails.
    this.sendChain = task.catch(() => undefined);
    return task;
  }

  private newId(): number {
    this.nextId += 1;
    return this.nextId;
  }

  private requireConnected(): ChildProcess {
    if (this.proc === null || this.proc.stdin === null) {
      throw new MCPConnectionError("Not connected. Call `await client.connect()` first.");
    }
    return this.proc;
  }

  private async onStdoutLine(line: string): Promise<void> {
    const trimmed = line.trim();
    if (!trimmed) {
      return;
    }
    let message: unknown;
    try {
      message = JSON.parse(trimmed);
    } catch {
      // Skip one malformed line; don't kill all pending requests.
      this.stderrLinesBuf.push(`Skipped non-JSON stdout line: ${trimmed.slice(0, 200)}`);
      if (this.stderrLinesBuf.length > 1000) {
        this.stderrLinesBuf.splice(0, this.stderrLinesBuf.length - 1000);
      }
      return;
    }
    if (typeof message !== "object" || message === null) {
      return;
    }
    await this.dispatch(message as Record<string, unknown>);
  }

  private async dispatch(message: Record<string, unknown>): Promise<void> {
    const hasId = "id" in message;
    const hasMethod = "method" in message;

    if (hasId && hasMethod) {
      // A request from the server to us. We implement none of them, but
      // we must reply so the server does not block waiting.
      await this.send({
        jsonrpc: "2.0",
        id: message["id"],
        error: { code: -32601, message: `Method not found: ${message["method"]}` },
      }).catch(() => undefined);
      return;
    }

    if (hasId) {
      const entry = this.pending.get(message["id"] as number);
      if (!entry) {
        return;
      }
      this.pending.delete(message["id"] as number);
      clearTimeout(entry.timer);
      if ("error" in message) {
        entry.reject(JSONRPCError.fromDict(message["error"] as { code?: number; message?: string; data?: unknown }));
      } else {
        entry.resolve((message["result"] as Record<string, unknown>) ?? {});
      }
      return;
    }

    // A notification (no id) — ignore for now.
  }

  private failAllPending(exc: unknown): void {
    for (const entry of this.pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(exc);
    }
    this.pending.clear();
  }

  /** The most recent stderr output from the server (for debugging). */
  get stderrLines(): string[] {
    return [...this.stderrLinesBuf];
  }

  get isConnected(): boolean {
    return this.proc !== null && (this.proc.exitCode === null) && this.initialized && !this.closed;
  }

  /** The set of tool names this client refuses to expose or call. */
  get disabledTools(): Set<string> {
    return new Set(this.disabledToolsSet);
  }
}
