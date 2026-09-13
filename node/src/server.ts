/**
 * A transparent stdio MCP server that proxies the Roblox Studio MCP.
 *
 * Any MCP client can point at this server instead of launching StudioMCP
 * directly. It presents the *same* tools and raw responses as StudioMCP, but
 * routes every request through {@link MCPClient} so the connection is owned
 * and managed by this package — a single upstream process, reused for the
 * server's whole lifetime.
 *
 * Run as a stdio server:
 *
 *     node ./dist/server.js
 */

import { createInterface } from "node:readline";
import { MCPClient } from "./client.js";
import { defaultArgs, defaultCommand, defaultShell } from "./roblox.js";

export type SendFn = (message: Record<string, unknown>) => void;

export interface ProxyClientLike {
  protocolVersion: string;
  capabilities: Record<string, unknown>;
  serverInfo: Record<string, unknown>;
  disabledTools?: Set<string>;
  request(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
  close(): Promise<void>;
}

export function sendMessage(message: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(message) + "\n");
}

export async function handleMessage(
  client: ProxyClientLike,
  message: Record<string, unknown>,
  send: SendFn = sendMessage,
): Promise<void> {
  if (!("method" in message)) {
    return; // not a request/notification; ignore
  }
  const method = message["method"];
  const hasId = "id" in message;

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
    return; // notifications: no response
  }

  if (method === "ping") {
    if (hasId) {
      send({ jsonrpc: "2.0", id: message["id"], result: {} });
    }
    return;
  }

  if (method === "tools/list") {
    const params = (message["params"] as Record<string, unknown>) ?? {};
    let result: Record<string, unknown>;
    try {
      result = await client.request(method as string, params);
    } catch (exc) {
      if (hasId) {
        send({ jsonrpc: "2.0", id: message["id"], error: { code: -32000, message: String(exc) } });
      }
      return;
    }
    if (hasId) {
      // Honor client-side disabledTools even though we proxy raw.
      const disabled = client.disabledTools ?? new Set<string>();
      if (disabled.size > 0 && typeof result === "object" && result !== null && Array.isArray(result["tools"])) {
        result = {
          ...result,
          tools: (result["tools"] as Array<Record<string, unknown>>).filter(
            (t) => !disabled.has(t["name"] as string),
          ),
        };
      }
      send({ jsonrpc: "2.0", id: message["id"], result });
    }
    return;
  }

  if (method === "tools/call") {
    const params = (message["params"] as Record<string, unknown>) ?? {};
    const name = (params as Record<string, unknown>)["name"];
    const disabled = client.disabledTools ?? new Set<string>();
    if (typeof name === "string" && disabled.has(name)) {
      if (hasId) {
        send({
          jsonrpc: "2.0",
          id: message["id"],
          error: { code: -32602, message: `Tool ${JSON.stringify(name)} is disabled.` },
        });
      }
      return;
    }
    let result: Record<string, unknown>;
    try {
      result = await client.request(method as string, params);
    } catch (exc) {
      if (hasId) {
        send({ jsonrpc: "2.0", id: message["id"], error: { code: -32000, message: String(exc) } });
      }
      return;
    }
    if (hasId) {
      send({ jsonrpc: "2.0", id: message["id"], result });
    }
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

/** Run the stdio proxy until stdin closes. */
export async function serve(client?: ProxyClientLike): Promise<void> {
  let owned = false;
  let active: ProxyClientLike;
  if (client) {
    active = client;
  } else {
    const real = new MCPClient(defaultCommand(), defaultArgs(), { shell: defaultShell() });
    await real.connect();
    active = real;
    owned = true;
  }

  try {
    await relayLoop(active);
  } finally {
    if (owned) {
      await active.close();
    }
  }
}

async function relayLoop(client: ProxyClientLike): Promise<void> {
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
        await handleMessage(client, message as Record<string, unknown>);
      }
    }
  } finally {
    rl.close();
  }
}

function isMainModule(): boolean {
  const entry = process.argv[1] ?? "";
  return entry.endsWith("/server.js") || entry.endsWith("\\server.js") || entry.endsWith("server.js");
}

if (isMainModule()) {
  serve().catch((err) => {
    console.error(err);
    process.exitCode = 1;
  });
}
