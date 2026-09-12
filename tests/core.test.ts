/**
 * Tests for the pure-logic parts of roblox-studio-mcp-node (no Studio required).
 *
 * Port of the Python `tests/test_core.py`. Run with:
 *
 *     npm test
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { CallToolResult, MCPClient, Tool } from "../src/index.js";
import { MCPConnectionError, MCPToolError } from "../src/index.js";
import {
  RobloxStudio,
  getSingleton,
  closeSingleton,
  __resetSingletonForTests,
  platformDefaults,
  defaultCommand,
  defaultShell,
  MACOS_COMMAND,
  WINDOWS_COMMAND,
  WINDOWS_ARGS,
} from "../src/roblox.js";
import * as robloxMod from "../src/roblox.js";
import { handleMessage } from "../src/server.js";

describe("Tool", () => {
  it("fromDict", () => {
    const tool = Tool.fromDict({
      name: "execute_luau",
      description: "run luau",
      inputSchema: {
        type: "object",
        properties: {
          code: { type: "string" },
          datamodel_type: { type: "string" },
          studio_id: { type: "string" },
        },
        required: ["code", "datamodel_type", "studio_id"],
      },
    });
    expect(tool.name).toBe("execute_luau");
    expect(tool.description).toBe("run luau");
    expect(tool.hasParameter("studio_id")).toBe(true);
    expect(tool.hasParameter("nope")).toBe(false);
    expect(tool.required).toEqual(["code", "datamodel_type", "studio_id"]);
  });
});

describe("CallToolResult", () => {
  it("concatenates text blocks", () => {
    const result = CallToolResult.fromDict({
      content: [
        { type: "text", text: "hello" },
        { type: "text", text: "world" },
      ],
    });
    expect(result.text()).toBe("hello\nworld");
  });

  it("parses plain JSON", () => {
    const result = CallToolResult.fromDict({
      content: [{ type: "text", text: '{"studios": [{"id": "a", "name": "Place1"}]}' }],
    });
    expect(result.json()).toEqual({ studios: [{ id: "a", name: "Place1" }] });
  });

  it("parses fenced JSON", () => {
    const result = CallToolResult.fromDict({
      content: [{ type: "text", text: '```json\n{"a": 1}\n```' }],
    });
    expect(result.json()).toEqual({ a: 1 });
  });

  it("parses JSON embedded in prose", () => {
    const result = CallToolResult.fromDict({
      content: [{ type: "text", text: "Here it is: [1, 2, 3] ok" }],
    });
    expect(result.json()).toEqual([1, 2, 3]);
  });
});

class FakeClient {
  calls: Array<[string, Record<string, unknown>]> = [];

  async listTools(): Promise<Tool[]> {
    return [
      new Tool("list_roblox_studios"), // no studio_id in schema
      Tool.fromDict({
        name: "execute_luau",
        inputSchema: {
          type: "object",
          properties: { code: { type: "string" }, studio_id: { type: "string" } },
          required: ["code", "studio_id"],
        },
      }),
    ];
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<CallToolResult> {
    this.calls.push([name, args]);
    return CallToolResult.fromDict({ content: [{ type: "text", text: "{}" }] });
  }

  get isConnected(): boolean {
    return true;
  }

  async close(): Promise<void> {}
}

class FlakyClient {
  attempts = 0;

  constructor(
    private failures: number = Number.POSITIVE_INFINITY,
    private mode: "not-ready" | "boom" | "empty" = "not-ready",
  ) {}

  async listTools(): Promise<Tool[]> {
    return [
      Tool.fromDict({
        name: "execute_luau",
        inputSchema: {
          type: "object",
          properties: { code: { type: "string" }, studio_id: { type: "string" } },
          required: ["code", "studio_id"],
        },
      }),
    ];
  }

  async callTool(name: string, _args: Record<string, unknown> = {}): Promise<CallToolResult> {
    if (name === "list_roblox_studios") {
      this.attempts += 1;
      if (this.mode === "boom") {
        throw new Error("boom");
      }
      if (this.attempts <= this.failures) {
        throw new MCPToolError(
          `Tool ${JSON.stringify(name)} reported an error: Unable to reach Roblox Studio right now.`,
        );
      }
      if (this.mode === "empty") {
        return CallToolResult.fromDict({ content: [{ type: "text", text: '{"studios": []}' }] });
      }
      return CallToolResult.fromDict({
        content: [{ type: "text", text: '{"studios": [{"id": "sid-9", "name": "P"}]}' }],
      });
    }
    return CallToolResult.fromDict({ content: [{ type: "text", text: "ok" }] });
  }

  get isConnected(): boolean {
    return true;
  }

  async close(): Promise<void> {}
}

describe("RobloxStudio", () => {
  it("injects studio_id when declared", async () => {
    const client = new FakeClient();
    const studio = new RobloxStudio(client, "sid-1");
    await studio.executeLuau("return 1");
    const [name, args] = client.calls[client.calls.length - 1];
    expect(name).toBe("execute_luau");
    expect(args["studio_id"]).toBe("sid-1");
    expect(args["code"]).toBe("return 1");
  });

  it("does not inject studio_id when absent", async () => {
    const client = new FakeClient();
    const studio = new RobloxStudio(client, "sid-1");
    await studio.call("list_roblox_studios", {});
    const [name, args] = client.calls[client.calls.length - 1];
    expect(name).toBe("list_roblox_studios");
    expect("studio_id" in args).toBe(false);
  });

  it("explicit studio_id wins", async () => {
    const client = new FakeClient();
    const studio = new RobloxStudio(client, "sid-1");
    await studio.call("execute_luau", { studio_id: "override", code: "x" });
    const [, args] = client.calls[client.calls.length - 1];
    expect(args["studio_id"]).toBe("override");
  });
});

describe("resolveReadiness", () => {
  it("rides through transient not-ready", async () => {
    const client = new FlakyClient(2);
    const studio = new RobloxStudio(client);
    const sid = await studio.resolveStudioId({ timeoutMs: 5000, intervalMs: 5 });
    expect(sid).toBe("sid-9");
    expect(client.attempts).toBe(3);
  });

  it("gives up after timeout", async () => {
    const client = new FlakyClient();
    const studio = new RobloxStudio(client);
    await expect(studio.resolveStudioId({ timeoutMs: 50, intervalMs: 5 })).rejects.toBeInstanceOf(
      MCPToolError,
    );
    expect(client.attempts).toBeGreaterThan(1);
  });

  it("other errors throw immediately", async () => {
    const client = new FlakyClient(0, "boom");
    const studio = new RobloxStudio(client);
    await expect(studio.resolveStudioId({ timeoutMs: 5000, intervalMs: 5 })).rejects.toThrow("boom");
    expect(client.attempts).toBe(1);
  });

  it("empty list throws immediately", async () => {
    const client = new FlakyClient(0, "empty");
    const studio = new RobloxStudio(client);
    await expect(studio.resolveStudioId({ timeoutMs: 5000, intervalMs: 5 })).rejects.toThrow(
      /No Roblox Studio instances/,
    );
    expect(client.attempts).toBe(1);
  });

  it("first tool call on a fresh client rides through", async () => {
    const client = new FlakyClient(2);
    const studio = new RobloxStudio(client, null, { isSingleton: true });
    const result = await studio.executeLuau("return 1 + 1");
    expect(result.text()).toBe("ok");
    expect(studio.studioId).toBe("sid-9");
    expect(client.attempts).toBe(3);
  });
});

describe("disabledTools", () => {
  it("exposes the disabled set", () => {
    const client = new MCPClient("cmd.exe", ["/c", "echo"], { disabledTools: ["foo", "bar"] });
    expect(client.disabledTools).toEqual(new Set(["foo", "bar"]));
  });

  it("callTool throws for disabled tools", async () => {
    const client = new MCPClient("cmd.exe", ["/c", "echo"], { disabledTools: ["foo"] });
    await expect(client.callTool("foo", {})).rejects.toBeInstanceOf(MCPToolError);
  });

  it("callTool proceeds for enabled tools", async () => {
    const client = new MCPClient("cmd.exe", ["/c", "echo"], { disabledTools: ["foo"] });
    // "bar" is not disabled, so it should get past the disabled check and
    // fail only because we are not connected (MCPConnectionError).
    await expect(client.callTool("bar", {})).rejects.toBeInstanceOf(MCPConnectionError);
  });
});

describe("singleton", () => {
  beforeEach(() => {
    __resetSingletonForTests();
    vi.restoreAllMocks();
  });

  it("returns the same instance", async () => {
    const connectSpy = vi
      .spyOn(MCPClient.prototype, "connect")
      .mockImplementation(async function (this: unknown) {
        const self = this as Record<string, unknown>;
        self["proc"] = { exitCode: null };
        self["initialized"] = true;
        return this as MCPClient;
      });
    try {
      const a = await getSingleton();
      const b = await getSingleton();
      expect(a).toBe(b);
    } finally {
      connectSpy.mockRestore();
      await closeSingleton().catch(() => undefined);
      __resetSingletonForTests();
    }
  });

  it("close resets", async () => {
    const connectSpy = vi
      .spyOn(MCPClient.prototype, "connect")
      .mockImplementation(async function (this: unknown) {
        const self = this as Record<string, unknown>;
        self["proc"] = { exitCode: null };
        self["initialized"] = true;
        return this as MCPClient;
      });
    const closeSpy = vi.spyOn(MCPClient.prototype, "close").mockResolvedValue(undefined);
    try {
      await getSingleton();
      await closeSingleton();
      expect(closeSpy).toHaveBeenCalledOnce();
    } finally {
      connectSpy.mockRestore();
      closeSpy.mockRestore();
      __resetSingletonForTests();
    }
  });

  it("reconnects when dropped", async () => {
    const connectSpy = vi
      .spyOn(MCPClient.prototype, "connect")
      .mockImplementation(async function (this: unknown) {
        const self = this as Record<string, unknown>;
        self["proc"] = { exitCode: null };
        self["initialized"] = true;
        return this as MCPClient;
      });
    try {
      const a = await getSingleton();
      // Simulate a dropped connection.
      (a.client as unknown as Record<string, unknown>)["initialized"] = false;
      const b = await getSingleton();
      expect(b).not.toBe(a);
      expect(connectSpy).toHaveBeenCalledTimes(2);
    } finally {
      connectSpy.mockRestore();
      await closeSingleton().catch(() => undefined);
      __resetSingletonForTests();
    }
  });

  it("connect defaults to singleton", async () => {
    // NOTE: connect() calls the module-internal getSingleton binding, which
    // cannot be intercepted by spying on the module namespace export, so
    // verify the contract behaviorally: two default connect() calls share one
    // connection instead of spawning a fresh proxy each time.
    const connectSpy = vi
      .spyOn(MCPClient.prototype, "connect")
      .mockImplementation(async function (this: unknown) {
        const self = this as Record<string, unknown>;
        self["proc"] = { exitCode: null };
        self["initialized"] = true;
        return this as MCPClient;
      });
    try {
      const a = await robloxMod.RobloxStudio.connect();
      const b = await robloxMod.RobloxStudio.connect();
      expect(a).toBe(b);
      expect(connectSpy).toHaveBeenCalledTimes(1);
    } finally {
      connectSpy.mockRestore();
      await closeSingleton().catch(() => undefined);
      __resetSingletonForTests();
    }
  });
});

describe("platformDefaults", () => {
  it("macOS uses the bundled StudioMCP binary directly, no shell", () => {
    expect(platformDefaults("darwin")).toEqual({
      command: "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP",
      args: [],
      shell: false,
    });
    expect(MACOS_COMMAND).toBe("/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP");
  });

  it("Windows keeps cmd + mcp.bat in shell mode", () => {
    expect(platformDefaults("win32")).toEqual({
      command: "cmd.exe",
      args: ["/c", '"cd /d %LOCALAPPDATA%\\Roblox && .\\mcp.bat"'],
      shell: true,
    });
    expect(WINDOWS_COMMAND).toBe("cmd.exe");
    expect([...WINDOWS_ARGS]).toEqual(platformDefaults("win32").args);
  });

  it("connect() wires platform defaults on macOS", async () => {
    const orig = process.platform;
    Object.defineProperty(process, "platform", { value: "darwin", configurable: true });
    const connectSpy = vi
      .spyOn(MCPClient.prototype, "connect")
      .mockImplementation(async function (this: unknown) {
        return this as MCPClient;
      });
    try {
      const studio = await robloxMod.RobloxStudio.connect({ singleton: false });
      const client = studio.client as unknown as Record<string, unknown>;
      expect(client["shell"]).toBe(false);
      expect(client["argv"]).toEqual(["/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP"]);
      expect(defaultCommand()).toBe("/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP");
      expect(defaultShell()).toBe(false);
      await studio.close();
    } finally {
      Object.defineProperty(process, "platform", { value: orig, configurable: true });
      connectSpy.mockRestore();
    }
  });
});

describe("server proxy", () => {
  it("initialize returns upstream info", async () => {
    const fakeClient = {
      protocolVersion: "2024-11-05",
      capabilities: { tools: { listChanged: true } },
      serverInfo: { name: "RobloxStudio", version: "1.0.0" },
      async request() {
        throw new Error("should not be called");
      },
      async close() {},
    };
    const sent: Record<string, unknown>[] = [];
    await handleMessage(
      fakeClient,
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      (m) => sent.push(m),
    );
    expect((sent[0]["result"] as Record<string, unknown>)["serverInfo"]).toMatchObject({ name: "RobloxStudio" });
  });

  it("tools/call is forwarded", async () => {
    const fakeClient = {
      protocolVersion: "2024-11-05",
      capabilities: {},
      serverInfo: {},
      async request() {
        return { content: [{ type: "text", text: "ok" }] };
      },
      async close() {},
    };
    const sent: Record<string, unknown>[] = [];
    await handleMessage(
      fakeClient,
      {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_luau", arguments: { code: "return 1" } },
      },
      (m) => sent.push(m),
    );
    const result = sent[0]["result"] as Record<string, unknown>;
    const content = result["content"] as Array<Record<string, unknown>>;
    expect(content[0]["text"]).toBe("ok");
  });
});
