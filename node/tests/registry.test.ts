import { mkdtempSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { CallToolResult, Tool } from "../src/types.js";
import { MCPToolError } from "../src/errors.js";
import {
  EXPIRE_AFTER_SECONDS,
  REGISTRY_VERSION,
  STALE_AFTER_SECONDS,
  listInstances,
  loadAll,
  parseIdentity,
  readInBandIdentity,
  record,
  registryPath,
  resolve,
} from "../src/extended/registry.js";
import type { RobloxStudio } from "../src/roblox.js";

function tempPath(): string {
  return join(mkdtempSync(join(tmpdir(), "registry-")), "studios.json");
}

function consoleOf(...lines: string[]): CallToolResult {
  return CallToolResult.fromDict({
    content: [{ type: "text", text: lines.join("\n") }],
  });
}

/** Minimal RobloxStudio stand-in: answers the two calls the reader makes. */
function fakeStudio(consoleLines: string[], failExecute = false): RobloxStudio {
  const executed: Array<Record<string, unknown>> = [];
  const stub = {
    executed,
    async call(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
      if (name === "execute_luau") {
        if (failExecute) throw new MCPToolError("execute_luau is unavailable here");
        executed.push(args);
        return CallToolResult.fromDict({ content: [{ type: "text", text: "nil" }] });
      }
      if (name === "get_console_output") return consoleOf(...consoleLines);
      throw new Error(`unexpected tool ${name}`);
    },
  };
  return stub as unknown as RobloxStudio;
}

function fakeListClient(studios: Array<Record<string, unknown>>): RobloxStudio {
  const inner = { id: "inner" };
  return {
    client: inner,
    async listStudios() {
      return studios;
    },
  } as unknown as RobloxStudio;
}

describe("identity channel order", () => {
  // `return` is primary, `print` is the fallback. Mirrors
  // `python/tests/test_registry.py::ChannelOrder`. These assert on the SEQUENCE
  // of tool calls, not just the result: a test that only checked the identity
  // would pass either way and would not notice the console write returning.
  function studioReturning(
    onExecute: (n: number) => CallToolResult | Error,
    consoleLines: string[],
  ): { stub: RobloxStudio; seen: string[] } {
    const seen: string[] = [];
    let executes = 0;
    const stub = {
      async call(name: string): Promise<CallToolResult> {
        seen.push(name);
        if (name === "execute_luau") {
          executes += 1;
          const outcome = onExecute(executes);
          if (outcome instanceof Error) throw outcome;
          return outcome;
        }
        return CallToolResult.fromDict({
          content: [{ type: "text", text: consoleLines.join("\n") }],
        });
      },
    };
    return { stub: stub as unknown as RobloxStudio, seen };
  }

  it("uses the return channel and writes nothing", async () => {
    const { stub, seen } = studioReturning(
      () => CallToolResult.fromDict({ content: [{ type: "text", text: "MCPID\t0_7\tPlace1\t0\t0" }] }),
      [],
    );
    const got = await readInBandIdentity(stub);
    expect(got?.debug_id).toBe("0_7");
    expect(seen).toEqual(["execute_luau"]);
  });

  it("falls back to print when the return channel is empty", async () => {
    const { stub, seen } = studioReturning(
      () => CallToolResult.fromDict({ content: [{ type: "text", text: "" }] }),
      ["MCPID\t0_9\tNamed\t7\t8"],
    );
    const got = await readInBandIdentity(stub);
    expect(got?.debug_id).toBe("0_9");
    expect(seen).toContain("get_console_output");
  });

  it("falls back when the return channel raises", async () => {
    const { stub } = studioReturning(
      (n) =>
        n === 1
          ? new Error("return channel unavailable")
          : CallToolResult.fromDict({ content: [{ type: "text", text: "" }] }),
      ["MCPID\t0_5\tAfterFailure\t1\t2"],
    );
    const got = await readInBandIdentity(stub);
    expect(got?.debug_id).toBe("0_5");
  });

  it("both channels share one parse", () => {
    // One parser, two channels - so a divergence cannot hide here.
    for (const text of ['MCPID\t0_1\tPlace1\t123\t456', '"MCPID\t0_1\tPlace1\t123\t456"']) {
      expect(parseIdentity(text), text).toEqual({
        debug_id: "0_1",
        name: "Place1",
        place_id: 123,
        game_id: 456,
      });
    }
    expect(parseIdentity("MCPID\t0_1\tOnlyTwo")).toBeNull();
    expect(parseIdentity("MCPID\t0_1\tP\tnope\tnope")?.place_id).toBe(0);
  });
});

describe("readInBandIdentity", () => {
  it("parses a tagged line", async () => {
    const studio = fakeStudio(["MCPID\t0_185967\tPlace1\t123\t456"]);
    expect(await readInBandIdentity(studio)).toEqual({
      debug_id: "0_185967",
      name: "Place1",
      place_id: 123,
      game_id: 456,
    });
  });

  it("reads in Edit mode", async () => {
    // A play session reports a different debug id, so this must be Edit.
    const studio = fakeStudio(["MCPID\t0_1\tX\t0\t0"]);
    await readInBandIdentity(studio);
    const executed = (studio as unknown as { executed: Array<Record<string, unknown>> }).executed;
    expect(executed[0]!["datamodel_type"]).toBe("Edit");
  });

  it("tolerates surrounding quoting and noise", () => {
    // get_console_output can leave the tool's own quoting in place, and a tab
    // inside the payload arrives literally rather than escaped.
    const studio = fakeStudio([
      "some unrelated output",
      '"MCPID\t0_9\tNamed\t7\t8"',
      "trailing noise",
    ]);
    return expect(readInBandIdentity(studio)).resolves.toEqual({
      debug_id: "0_9",
      name: "Named",
      place_id: 7,
      game_id: 8,
    });
  });

  it("returns null when absent", async () => {
    expect(await readInBandIdentity(fakeStudio(["nothing tagged here"]))).toBeNull();
  });

  it("ignores a truncated line", async () => {
    expect(await readInBandIdentity(fakeStudio(["MCPID\t0_1\tOnlyTwo"]))).toBeNull();
  });
});

describe("record", () => {
  const identity = { debug_id: "0_1", name: "A", place_id: 0, game_id: 0 };

  it("keys by debug id", () => {
    const path = tempPath();
    record({ studioId: "sid-1", identity, path });
    expect(Object.keys(loadAll(path).instances)).toEqual(["0_1"]);
  });

  it("falls back to a session key without identity", () => {
    const path = tempPath();
    record({ studioId: "sid-9", identity: null, path });
    expect(Object.keys(loadAll(path).instances)).toEqual(["session:sid-9"]);
  });

  it("records id churn under a stable key", () => {
    const path = tempPath();
    record({ studioId: "old", identity, path });
    const entry = record({ studioId: "new", identity, path });
    expect(entry.debug_id).toBe("0_1");
    expect(entry.id_changed).toBe(true);
    expect(entry.studio_id_history).toEqual(["old", "new"]);
    // still one entry: the identity did not change, only the proxy id
    expect(Object.keys(loadAll(path).instances)).toHaveLength(1);
  });

  it("does not crash on a corrupt file", () => {
    const path = tempPath();
    writeFileSync(path, "{not json", "utf8");
    record({ studioId: "sid-1", identity, path });
    expect(Object.keys(loadAll(path).instances)).toEqual(["0_1"]);
  });

  it("leaves a foreign version's file alone rather than reading it", () => {
    const path = tempPath();
    writeFileSync(path, JSON.stringify({ version: 999, instances: { x: {} } }), "utf8");
    expect(loadAll(path).instances).toEqual({});
  });

  it("drops entries unseen for 30 days on record", () => {
    // Every restart orphans its old entry (new debug_id, new key), so without
    // pruning the file grows forever - 7 stored for 2 live, measured. A
    // 30-day-unseen entry is history no caller will ask about.
    const path = tempPath();
    const stale = {
      debug_id: "0_old",
      name: "Gone",
      place_id: 0,
      game_id: 0,
      last_studio_id: "sid-old",
      last_seen: Date.now() / 1000 - EXPIRE_AFTER_SECONDS - 60,
      studio_id_history: ["sid-old"],
      id_changed: false,
    };
    writeFileSync(
      path,
      JSON.stringify({ version: REGISTRY_VERSION, instances: { "0_old": stale } }),
      "utf8",
    );
    record({
      studioId: "sid-new",
      identity: { debug_id: "0_new", name: "Here", place_id: 0, game_id: 0 },
      path,
    });
    const keys = Object.keys(loadAll(path).instances);
    expect(keys).not.toContain("0_old");
    expect(keys).toContain("0_new");
  });

  it("keeps recent entries across a record", () => {
    const path = tempPath();
    record({
      studioId: "sid-a",
      identity: { debug_id: "0_a", name: "A", place_id: 0, game_id: 0 },
      path,
    });
    // Backdate just inside the window, then record again.
    const data = JSON.parse(readFileSync(path, "utf8")) as {
      instances: Record<string, { last_seen: number }>;
    };
    data.instances["0_a"]!.last_seen = Date.now() / 1000 - 60;
    writeFileSync(path, JSON.stringify(data), "utf8");
    record({
      studioId: "sid-b",
      identity: { debug_id: "0_b", name: "B", place_id: 0, game_id: 0 },
      path,
    });
    const keys = Object.keys(loadAll(path).instances);
    expect(keys).toContain("0_a");
    expect(keys).toContain("0_b");
  });

  it("drops non-object entries rather than crashing on them", () => {
    // Mirrors the Python case: the file may hold hand edits or foreign shapes,
    // and record() runs on every refreshing list. A non-object value reads
    // last_seen as undefined - infinitely old, never immortal.
    const path = tempPath();
    writeFileSync(
      path,
      JSON.stringify({ version: REGISTRY_VERSION, instances: { junk: [1, 2], junk2: "x" } }),
      "utf8",
    );
    record({
      studioId: "sid-fresh",
      identity: { debug_id: "0_f", name: "F", place_id: 0, game_id: 0 },
      path,
    });
    const keys = Object.keys(loadAll(path).instances);
    expect(keys).not.toContain("junk");
    expect(keys).not.toContain("junk2");
    expect(keys).toContain("0_f");
  });
});

describe("resolve", () => {
  function seed(path: string, pairs: Array<[string, string]>): void {
    for (const [debugId, name] of pairs) {
      record({ studioId: `sid-${debugId}`, identity: { debug_id: debugId, name, place_id: 0, game_id: 0 }, path });
    }
  }

  it("matches an exact debug id", () => {
    const path = tempPath();
    seed(path, [
      ["0_1", "A"],
      ["0_2", "B"],
    ]);
    const hit = resolve({ debugId: "0_2" }, path) as { status: string; match: { name: string } };
    expect(hit.status).toBe("ok");
    expect(hit.match.name).toBe("B");
  });

  it("reports an unknown debug id", () => {
    const path = tempPath();
    seed(path, [["0_1", "A"]]);
    const miss = resolve({ debugId: "nope" }, path) as Record<string, unknown>;
    expect(miss.status).toBe("not_found");
    expect(miss.known).toBeDefined();
  });

  it("refuses to guess when ambiguous", () => {
    const path = tempPath();
    seed(path, [
      ["0_1", "A"],
      ["0_2", "B"],
    ]);
    const result = resolve({}, path) as Record<string, unknown>;
    expect(result.status).toBe("ambiguous");
    expect((result.candidates as unknown[]).length).toBe(2);
    expect(result.match).toBeUndefined();
  });

  it("name disambiguates", () => {
    const path = tempPath();
    seed(path, [
      ["0_1", "A"],
      ["0_2", "B"],
    ]);
    const hit = resolve({ name: "B" }, path) as { status: string; match: { debug_id: string } };
    expect(hit.status).toBe("ok");
    expect(hit.match.debug_id).toBe("0_2");
  });

  it("duplicate names stay ambiguous", () => {
    const path = tempPath();
    seed(path, [
      ["0_1", "Same"],
      ["0_2", "Same"],
    ]);
    expect((resolve({ name: "Same" }, path) as { status: string }).status).toBe("ambiguous");
  });

  it("flags stale entries instead of hiding them", () => {
    const path = tempPath();
    record({ studioId: "sid-old", identity: { debug_id: "0_1", name: "A", place_id: 0, game_id: 0 }, path });
    // rewrite last_seek far in the past by hand
    const data = loadAll(path);
    data.instances["0_1"]!.last_seen = Date.now() / 1000 - STALE_AFTER_SECONDS - 60;
    writeFileSync(path, JSON.stringify(data), "utf8");
    const hit = resolve({ debugId: "0_1" }, path) as { status: string; match: { stale: boolean } };
    expect(hit.status).toBe("ok");
    expect(hit.match.stale).toBe(true);
  });
});

describe("listInstances", () => {
  it("merges the proxy list with in-band identity", async () => {
    const path = tempPath();
    const client = fakeListClient([
      { id: "sid-1", name: "A" },
      { id: "sid-2", name: "B" },
    ]);
    const result = (await listInstances(client, { path })) as {
      count: number;
      registered_total: number;
      instances: Array<{ reported_name: string; registered: boolean }>;
    };
    expect(result.count).toBe(2);
    expect(result.instances.map((e) => e.reported_name).sort()).toEqual(["A", "B"]);
    expect(result.instances.every((e) => e.registered)).toBe(true);
    expect(result.registered_total).toBe(2);
  });

  it("refresh=false does not write", async () => {
    const path = tempPath();
    await listInstances(fakeListClient([{ id: "sid-1", name: "A" }]), { refresh: false, path });
    expect(existsSync(path)).toBe(false);
  });
});

describe("registryPath", () => {
  it("honours the env override", () => {
    const previous = process.env["ROBLOX_STUDIO_MCP_REGISTRY"];
    process.env["ROBLOX_STUDIO_MCP_REGISTRY"] = "C:/tmp/x.json";
    try {
      expect(registryPath()).toBe("C:/tmp/x.json");
    } finally {
      if (previous === undefined) delete process.env["ROBLOX_STUDIO_MCP_REGISTRY"];
      else process.env["ROBLOX_STUDIO_MCP_REGISTRY"] = previous;
    }
  });

  it("defaults outside the project tree", () => {
    const previous = process.env["ROBLOX_STUDIO_MCP_REGISTRY"];
    delete process.env["ROBLOX_STUDIO_MCP_REGISTRY"];
    try {
      const path = registryPath();
      expect(path.endsWith(join("roblox-studio-mcp", "studios.json"))).toBe(true);
      expect(path).not.toContain("Source");
    } finally {
      if (previous !== undefined) process.env["ROBLOX_STUDIO_MCP_REGISTRY"] = previous;
    }
  });
});

describe("registry version", () => {
  it("is exported for the file format", () => {
    expect(REGISTRY_VERSION).toBe(1);
  });
});

// keep the Tool import used so the type surface stays exercised
void Tool;
void readFileSync;
