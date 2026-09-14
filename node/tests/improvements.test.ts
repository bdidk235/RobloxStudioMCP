/**
 * Regression tests for robustness fixes (no Studio required).
 *
 * Port of the Python `tests/test_improvements.py`: writer long-bracket
 * handling, updater ambiguity, console-watch diffing, create_module wiring,
 * search_and_read batching, run_tests polling, JSON parsing, singleton
 * ownership, and server disabled-tools filtering.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { CallToolResult, Tool } from "../src/index.js";
import * as writerMod from "../src/extended/writer.js";
import {
  luaLongBracket,
  pickBracketLevel,
  stripLinePrefixes,
  STRING_PROPERTY_SIZE_LIMIT,
  writeLikeMultiEdit,
} from "../src/extended/writer.js";
import { updateLikeMultiEdit } from "../src/extended/updater.js";
import * as extMod from "../src/extended/extensions.js";
import {
  ConsoleWatch,
  createModuleWithDeps,
  getWatchState,
  runTests,
  scriptSearchAndRead,
} from "../src/extended/extensions.js";
import { extractBalanced } from "../src/types.js";
import * as robloxMod from "../src/roblox.js";
import { handleMessage } from "../src/server.js";
import type { RobloxStudio } from "../src/roblox.js";

function textResult(text: string): CallToolResult {
  return CallToolResult.fromDict({ content: [{ type: "text", text }] });
}

/** Minimal RobloxStudio double recording calls. */
class FakeStudio {
  files: Record<string, string>;
  calls: Array<[string, Record<string, unknown>]> = [];
  executed: string[] = [];
  studioIdValue: string | null;
  started = 0;
  stopped = 0;
  consoleLines: string[] = ["ok line"];
  failExecuteWith: string | null = null;
  treePayload: unknown = [];

  constructor(files: Record<string, string> = {}, studioId: string | null = null) {
    this.files = { ...files };
    this.studioIdValue = studioId;
  }

  get studioId(): string | null {
    return this.studioIdValue;
  }

  get isConnected(): boolean {
    return true;
  }

  async close(): Promise<void> {}

  async listTools(): Promise<Tool[]> {
    return [];
  }

  async callTool(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
    return this.call(name, args);
  }

  async scriptRead(targetFile?: string, extra: Record<string, unknown> = {}): Promise<CallToolResult> {
    const p = targetFile ?? (extra["target_path"] as string) ?? (extra["path"] as string);
    this.calls.push(["script_read", { targetFile: p }]);
    if (p in this.files) {
      const src = this.files[p];
      const numbered = src.split("\n").map((line, i) => `${i + 1}→${line}`).join("\n");
      return textResult(numbered);
    }
    throw new Error(`Script not found: ${p}`);
  }

  async executeLuau(code: string): Promise<CallToolResult> {
    this.executed.push(code);
    if (this.failExecuteWith) {
      throw new Error(this.failExecuteWith);
    }
    return textResult("ok");
  }

  async call(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
    this.calls.push([name, { ...args }]);
    if (name === "multi_edit") {
      // Simulate atomic replace for writer/updater tests (and mutate files).
      const fp = args["file_path"] as string;
      const edits = args["edits"] as Array<{ old_string: string; new_string: string }>;
      if (fp in this.files) {
        let src = this.files[fp];
        for (const e of edits) {
          src = src.replace(e.old_string, e.new_string);
        }
        this.files[fp] = src;
      }
      return textResult("edited");
    }
    if (name === "get_console_output") {
      return textResult(this.consoleLines.join("\n"));
    }
    if (name === "list_roblox_studios") {
      return textResult(JSON.stringify({ studios: [] }));
    }
    return textResult("{}");
  }

  async searchGameTree(extra: Record<string, unknown> = {}): Promise<CallToolResult> {
    this.calls.push(["search_game_tree", { ...extra }]);
    return textResult(JSON.stringify(this.treePayload));
  }

  async startPlay(): Promise<CallToolResult> {
    this.started += 1;
    return textResult("started");
  }

  async stopPlay(): Promise<CallToolResult> {
    this.stopped += 1;
    return textResult("stopped");
  }

  asStudio(): RobloxStudio {
    return this as unknown as RobloxStudio;
  }
}

describe("stripLinePrefixes", () => {
  it("strips numbered prefix", () => {
    expect(stripLinePrefixes("1→print(1)\n2→print(2)")).toBe("print(1)\nprint(2)");
  });

  it("preserves arrow in source", () => {
    // Literal → without a leading line number must survive.
    expect(stripLinePrefixes("1→a → b")).toBe("a → b");
    expect(stripLinePrefixes("x → y")).toBe("x → y");
  });

  it("tolerates whitespace", () => {
    expect(stripLinePrefixes("  12  →hi")).toBe("hi");
  });
});

describe("long brackets", () => {
  it("raw payload needs no escapes", () => {
    const payload = "a'b\"c\\d\nnewline → arrow";
    const level = pickBracketLevel(payload);
    const lit = luaLongBracket(payload, level);
    expect(lit).toContain(payload);
    expect(lit.startsWith("[")).toBe(true);
    expect(lit.endsWith("]")).toBe(true);
  });

  it("picks a collision-free level", () => {
    const colliding = "x ]==========] y"; // closes level-10
    const level = pickBracketLevel("name", colliding);
    expect(level).not.toBe(10);
    const lit = luaLongBracket(colliding, level);
    expect(lit).toContain(colliding);
    // Outer bracket must use the new level, so the inner level-10 closer
    // can't terminate it early.
    expect(lit.startsWith(`[${"=".repeat(level)}[`)).toBe(true);
    expect(lit.endsWith(`]${"=".repeat(level)}]`)).toBe(true);
  });

  it("chunked builder uses raw name", () => {
    const lua = writerMod.buildChunkedLua("game.S", 'a"b', "Script", ["print(1)"], 10);
    expect(lua).toContain('FindFirstChild([==========[a"b]==========])');
  });

  it("guards leading newline in long brackets", () => {
    // Lua skips the first character of a long-bracket string when it is
    // a newline; the guard newline is consumed instead of the payload.
    const lit = luaLongBracket("\nabc", 10);
    expect(lit.startsWith("[==========[\n")).toBe(true);
    expect(lit).toContain("\nabc");
  });

  it("adds no guard without leading newline", () => {
    expect(luaLongBracket("abc", 10)).toBe("[==========[abc]==========]");
  });

  it("chunked builder captures loop index", () => {
    const lua = writerMod.buildChunkedLua("game.S", "A", "Script", ["x"], 10);
    expect(lua).toContain("local idx = i");
    expect(lua).toContain("if idx == 1");
    expect(lua).not.toContain("if i == 1");
  });
});

describe("writer", () => {
  it("rejects non-game paths", async () => {
    const studio = new FakeStudio();
    await expect(writeLikeMultiEdit(studio.asStudio(), "/tmp/x.lua", "hi")).rejects.toThrow();
  });

  it("rejects bad class", async () => {
    const studio = new FakeStudio({ "game.S.A": "hi" });
    await expect(writeLikeMultiEdit(studio.asStudio(), "game.S.A", "hi2", { className: "Part" })).rejects.toThrow();
  });

  it("unchanged performs no write", async () => {
    const studio = new FakeStudio({ "game.S.A": "print(1)" });
    const status = await writeLikeMultiEdit(studio.asStudio(), "game.S.A", "print(1)");
    expect(status).toBe("unchanged");
    expect(studio.calls.some(([c]) => c === "multi_edit")).toBe(false);
  });

  it("writes via multi_edit", async () => {
    const studio = new FakeStudio({ "game.S.A": "old" });
    const status = await writeLikeMultiEdit(studio.asStudio(), "game.S.A", "new");
    expect(status).toBe("wrote");
    expect(studio.calls.some(([c]) => c === "multi_edit")).toBe(true);
  });

  it("writes control bytes by default", async () => {
    const studio = new FakeStudio({ "game.S.A": "old" });
    await writeLikeMultiEdit(studio.asStudio(), "game.S.A", "a\nb");
    expect(studio.files["game.S.A"]).toBe("a\nb");
  });

  it("creates when missing", async () => {
    const studio = new FakeStudio({});
    const status = await writeLikeMultiEdit(studio.asStudio(), "game.S.New", "print('hi')", {
      createIfMissing: true,
    });
    expect(status).toBe("created");
    expect(studio.executed.length).toBeGreaterThan(0);
    // Long-bracket source, not single-quote escapes.
    expect(studio.executed[0]).toContain("[==========[");
  });

  it("missing requires flag", async () => {
    const studio = new FakeStudio({});
    await expect(writeLikeMultiEdit(studio.asStudio(), "game.S.New", "x")).rejects.toThrow();
  });

  it("does not mask connection errors", async () => {
    const studio = new FakeStudio({});
    studio.scriptRead = async () => {
      throw new Error("connection reset by peer");
    };
    await expect(
      writeLikeMultiEdit(studio.asStudio(), "game.S.New", "x", { createIfMissing: true }),
    ).rejects.toThrow();
    expect(studio.executed).toEqual([]);
  });

  it("returnString uses long brackets", async () => {
    const studio2 = new FakeStudio({});
    const status = await writeLikeMultiEdit(studio2.asStudio(), "game.S.M", 'a"b\nc', {
      createIfMissing: true,
      returnString: true,
    });
    expect(status).toBe("created");
    const code = studio2.executed[0];
    expect(code).toContain("return");
    expect(code).toContain("[==========[");
    expect(code).not.toContain('\\"');
  });

  it("chunked retry is capped", async () => {
    const studio = new FakeStudio({ "game.S.Big": "old" });
    const big = "x".repeat(STRING_PROPERTY_SIZE_LIMIT + 10);
    studio.failExecuteWith = "Lua parse error near slices";
    await expect(writerMod.chunkedWrite(studio.asStudio(), "game.S.Big", big)).rejects.toThrow();
    // 1 initial + 5 retries
    expect(studio.executed.length).toBe(6);
  });
});

describe("updater", () => {
  it("skips no-op and missing edits", async () => {
    const studio = new FakeStudio({ "game.S.A": "hello world" });
    const result = await updateLikeMultiEdit(studio.asStudio(), "game.S.A", [
      ["hello", "hi"],
      ["same", "same"],
      ["nope", "x"],
    ]);
    expect(result.updated).toEqual([0]);
    expect(result.skippedNoOp).toEqual([1]);
    expect(result.skippedNoMatch).toEqual([2]);
    expect(result.warnings.length).toBeGreaterThan(0);
  });

  it("skips ambiguous duplicates", async () => {
    const studio = new FakeStudio({ "game.S.A": "foo foo foo" });
    const result = await updateLikeMultiEdit(studio.asStudio(), "game.S.A", [["foo", "bar"]]);
    expect(result.skippedAmbiguous).toEqual([0]);
    expect(result.updated).toEqual([]);
    // Nothing sent downstream when all edits skipped.
    expect(studio.calls.some(([c]) => c === "multi_edit")).toBe(false);
  });

  it("strict mode raises on ambiguous", async () => {
    const studio = new FakeStudio({ "game.S.A": "foo foo" });
    await expect(
      updateLikeMultiEdit(studio.asStudio(), "game.S.A", [["foo", "bar"]], { skipMissing: false }),
    ).rejects.toThrow();
  });

  it("strict mode raises on no-op", async () => {
    const studio = new FakeStudio({ "game.S.A": "hi" });
    await expect(
      updateLikeMultiEdit(studio.asStudio(), "game.S.A", [["hi", "hi"]], { skipNoOps: false }),
    ).rejects.toThrow();
  });

  it("replace_all replaces every occurrence like Edit replaceAll", async () => {
    const studio = new FakeStudio({ "game.S.A": "foo foo foo" });
    const result = await updateLikeMultiEdit(studio.asStudio(), "game.S.A", [
      { old_string: "foo", new_string: "bar", replace_all: true },
    ]);
    expect(result.updated).toEqual([0]);
    expect(result.skippedAmbiguous).toEqual([]);
    expect(studio.files["game.S.A"]).toBe("bar bar bar");
  });

  it("replaceAll camelCase alias works", async () => {
    const studio = new FakeStudio({ "game.S.A": "a,a,a" });
    const result = await updateLikeMultiEdit(studio.asStudio(), "game.S.A", [
      { oldString: "a", newString: "b", replaceAll: true },
    ]);
    expect(result.updated).toEqual([0]);
    expect(studio.files["game.S.A"]).toBe("b,b,b");
  });

  it("sequential edits operate on previous results", async () => {
    const studio = new FakeStudio({ "game.S.A": "hello world" });
    const result = await updateLikeMultiEdit(studio.asStudio(), "game.S.A", [
      { old_string: "hello", new_string: "hi", replace_all: true },
      { old_string: "hi world", new_string: "hi there" },
    ]);
    expect(result.updated).toEqual([0, 1]);
    expect(studio.files["game.S.A"]).toBe("hi there");
  });

  it("strict errors read like Claude Code Edit errors", async () => {
    const missing = new FakeStudio({ "game.S.A": "hello" });
    await expect(
      updateLikeMultiEdit(missing.asStudio(), "game.S.A", [["nope", "x"]], { skipMissing: false }),
    ).rejects.toThrow("not found in content");
    const ambiguous = new FakeStudio({ "game.S.A": "foo foo" });
    await expect(
      updateLikeMultiEdit(ambiguous.asStudio(), "game.S.A", [["foo", "bar"]], { skipMissing: false }),
    ).rejects.toThrow("multiple matches");
  });
});

describe("extended tool descriptions", () => {
  it("recommend extended tools over raw multi_edit", async () => {
    const { EXTENDED_TOOLS } = await import("../src/extendedServer.js");
    const byName = new Map(EXTENDED_TOOLS.map((t) => [t.name, t.description]));
    expect(byName.get("extended_write_like_multi_edit")).toMatch(/^RECOMMENDED over raw multi_edit/);
    expect(byName.get("extended_update_like_multi_edit")).toMatch(/^RECOMMENDED over raw multi_edit/);
    expect(byName.get("extended_update_like_multi_edit")).toContain("replace_all");
    const schema = EXTENDED_TOOLS.find((t) => t.name === "extended_update_like_multi_edit")?.inputSchema as Record<
      string,
      Record<string, Record<string, unknown>>
    >;
    const itemProps = (
      (schema["properties"]?.["edits"] as Record<string, unknown>)?.["items"] as Record<string, unknown>
    )?.["properties"] as Record<string, unknown>;
    expect(itemProps).toHaveProperty("replace_all");
  });

  it("annotates proxied multi_edit to steer toward extended tools", async () => {
    const { handleExtendedMessage } = await import("../src/extendedServer.js");
    const fakeClient = {
      protocolVersion: "x",
      capabilities: {},
      serverInfo: {},
      async request() {
        return {
          tools: [{ name: "multi_edit", description: "Raw edits.", inputSchema: { type: "object" } }],
        };
      },
      async close() {},
    } as unknown as Parameters<typeof handleExtendedMessage>[0];
    const sent: Record<string, unknown>[] = [];
    await handleExtendedMessage(
      fakeClient,
      { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
      (m) => sent.push(m),
    );
    const tools = (sent[0]["result"] as Record<string, unknown>)["tools"] as Array<Record<string, unknown>>;
    const multi = tools.find((t) => t["name"] === "multi_edit");
    expect(String(multi?.["description"])).toContain("prefer extended_write_like_multi_edit");
    expect(tools.some((t) => t["name"] === "extended_write_like_multi_edit")).toBe(true);
  });
});

describe("ConsoleWatch", () => {
  it("first call returns tail", async () => {
    const studio = new FakeStudio();
    studio.consoleLines = Array.from({ length: 50 }, (_, i) => `line ${i}`);
    const watch = new ConsoleWatch();
    const out = await watch.poll(studio.asStudio());
    expect(out.newLines.length).toBe(20);
    expect(out.totalLines).toBe(50);
  });

  it("append-only returns delta", async () => {
    const studio = new FakeStudio();
    const watch = new ConsoleWatch();
    studio.consoleLines = ["a", "b"];
    await watch.poll(studio.asStudio());
    studio.consoleLines = ["a", "b", "c"];
    const out = await watch.poll(studio.asStudio());
    expect(out.newLines).toEqual(["c"]);
  });

  it("duplicate last line uses last occurrence", async () => {
    const watch = new ConsoleWatch();
    watch._setLastLinesForTests(["x", "dup", "y"]);
    const studio = new FakeStudio();
    studio.consoleLines = ["x", "dup", "y", "dup", "z"];
    const out = await watch.poll(studio.asStudio());
    // Fast path fails (prefix differs), fallback finds last "y" at index 2.
    expect(out.newLines).toEqual(["dup", "z"]);
  });

  it("truncation returns all", async () => {
    const watch = new ConsoleWatch();
    watch._setLastLinesForTests(["old1", "old2"]);
    const studio = new FakeStudio();
    studio.consoleLines = ["new1"];
    const out = await watch.poll(studio.asStudio());
    expect(out.newLines).toEqual(["new1"]);
  });

  it("getWatchState is persistent per key", () => {
    extMod.__clearWatchStatesForTests();
    expect(getWatchState("s1")).toBe(getWatchState("s1"));
    expect(getWatchState("s1")).not.toBe(getWatchState("s2"));
  });
});

describe("createModule", () => {
  it("appends require once", async () => {
    const studio = new FakeStudio({
      "game.ReplicatedStorage.Util": "return {}",
      "game.ServerScriptService.Main": "print(1)\n",
      "game.ReplicatedStorage.NewMod": "old",
    });
    const status = await createModuleWithDeps(studio.asStudio(), "game.ReplicatedStorage.NewMod", "return 42", {
      requireTargetPath: "game.ServerScriptService.Main",
      requireStatement: "require(game.ReplicatedStorage.NewMod)",
    });
    expect(["wrote", "created", "unchanged"]).toContain(status);
    expect(studio.files["game.ServerScriptService.Main"]).toContain("require(game.ReplicatedStorage.NewMod)");
    // Idempotent: second call must not duplicate the require.
    await createModuleWithDeps(studio.asStudio(), "game.ReplicatedStorage.NewMod", "return 42", {
      requireTargetPath: "game.ServerScriptService.Main",
      requireStatement: "require(game.ReplicatedStorage.NewMod)",
    });
    const occurrences = studio.files["game.ServerScriptService.Main"].split("require(game.ReplicatedStorage.NewMod)").length - 1;
    expect(occurrences).toBe(1);
  });

  it("skips when require already present", async () => {
    const req = "require(game.ReplicatedStorage.Util)";
    const studio = new FakeStudio({
      "game.S.Main": `print(1)\n${req}\n`,
      "game.S.Mod": "old",
    });
    await createModuleWithDeps(studio.asStudio(), "game.S.Mod", "old", {
      requireTargetPath: "game.S.Main",
      requireStatement: req,
    });
    // Only the module write path touched multi_edit/execute; target unchanged.
    expect(studio.files["game.S.Main"].split(req).length - 1).toBe(1);
  });
});

describe("searchAndRead", () => {
  it("batches and filters", async () => {
    const studio = new FakeStudio();
    studio.treePayload = [
      { className: "Script", fullPath: "game.S.A", name: "A" },
      { className: "Part", fullPath: "game.S.P", name: "P" },
      { className: "ModuleScript", fullPath: "game.S.M", name: "M" },
    ];
    studio.files = { "game.S.A": "a-src", "game.S.M": "m-src" };
    const out = await scriptSearchAndRead(studio.asStudio(), "game.S", { maxResults: 10 });
    expect(out.map((r) => r.path)).toEqual(["game.S.A", "game.S.M"]);
  });

  it("truncates long sources by default with line counts", async () => {
    const studio = new FakeStudio();
    studio.treePayload = [{ className: "Script", fullPath: "game.S.Big", name: "Big" }];
    const lines = Array.from({ length: 100 }, (_, i) => `print(${i}) -- padding to grow the source`);
    studio.files = { "game.S.Big": lines.join("\n") };
    const out = await scriptSearchAndRead(studio.asStudio(), "game.S");
    expect(out).toHaveLength(1);
    expect(out[0].truncated).toBe(true);
    expect(out[0].source).toHaveLength(2000);
    expect(out[0].line_count).toBe(100);
  });

  it("returns full source when truncation is disabled", async () => {
    const studio = new FakeStudio();
    studio.treePayload = [{ className: "Script", fullPath: "game.S.Big", name: "Big" }];
    const lines = Array.from({ length: 100 }, (_, i) => `print(${i}) -- padding to grow the source`);
    const full = lines.join("\n");
    studio.files = { "game.S.Big": full };
    const out = await scriptSearchAndRead(studio.asStudio(), "game.S", { maxCharsPerSource: 0 });
    expect(out).toHaveLength(1);
    expect(out[0].truncated).toBe(false);
    expect(out[0].source).toBe(full);
    expect(out[0].line_count).toBe(100);
  });

  it("returns empty on bad JSON", async () => {
    const studio = new FakeStudio();
    studio.treePayload = null;
    studio.searchGameTree = async () => textResult("not json at all {{{");
    const out = await scriptSearchAndRead(studio.asStudio(), "game.S");
    expect(out).toEqual([]);
  });
});

describe("runTests", () => {
  it("passes without errors", async () => {
    const studio = new FakeStudio();
    studio.consoleLines = ["info: ok", "0 errors"];
    const out = await runTests(studio.asStudio(), { waitSeconds: 0.01 });
    expect(out.passed).toBe(true);
    expect(studio.started).toBe(1);
    expect(studio.stopped).toBe(1);
  });

  it("detects errors case-insensitively", async () => {
    const studio = new FakeStudio();
    studio.consoleLines = ["SCRIPT ERROR: boom", "done"];
    const out = await runTests(studio.asStudio(), { waitSeconds: 0.01 });
    expect(out.passed).toBe(false);
    expect(out.errors.length).toBeGreaterThan(0);
  });

  it("missing test paths fail", async () => {
    const studio = new FakeStudio({});
    studio.consoleLines = ["all good"];
    const out = await runTests(studio.asStudio(), { testPaths: ["game.S.Missing"], waitSeconds: 0.01 });
    expect(out.passed).toBe(false);
    expect(out.errors.some((e) => e.includes("Missing"))).toBe(true);
  });

  it("always stops play", async () => {
    const studio = new FakeStudio();
    studio.consoleLines = ["x"];
    studio.call = async () => {
      throw new Error("console exploded");
    };
    await expect(runTests(studio.asStudio(), { waitSeconds: 0.01 })).rejects.toThrow();
    expect(studio.stopped).toBe(1);
  });
});

describe("JSON parsing", () => {
  const res = (text: string) => CallToolResult.fromDict({ content: [{ type: "text", text }] });

  it("fenced with lang", () => {
    expect(res('```json\n{"a": 1}\n```').json()).toEqual({ a: 1 });
  });

  it("fenced without lang", () => {
    expect(res("```\n[1,2]\n```").json()).toEqual([1, 2]);
  });

  it("embedded prefers first balanced", () => {
    expect(res('Here: {"a": {"b": 1}} tail {"c": 2}').json()).toEqual({ a: { b: 1 } });
  });

  it("braces in strings", () => {
    expect(res('x {"a": "} {"} y').json()).toEqual({ a: "} {" });
  });

  it("null when no JSON", () => {
    expect(res("just prose").json()).toBeNull();
  });

  it("extractBalanced", () => {
    expect(extractBalanced('{"a":1} tail')).toBe('{"a":1}');
    expect(extractBalanced("nope")).toBeNull();
  });
});

describe("singleton ownership", () => {
  beforeEach(() => {
    robloxMod.__resetSingletonForTests();
  });

  it("asyncDispose keeps singleton", async () => {
    const fakeClient = { close: vi.fn() } as unknown as robloxMod.StudioClientLike;
    const studio = new robloxMod.RobloxStudio(fakeClient, null, { isSingleton: true });
    await studio[Symbol.asyncDispose]();
    expect(fakeClient.close).not.toHaveBeenCalled();
  });

  it("asyncDispose closes owned connections", async () => {
    const fakeClient = { close: vi.fn().mockResolvedValue(undefined) } as unknown as robloxMod.StudioClientLike;
    const studio = new robloxMod.RobloxStudio(fakeClient, null, { isSingleton: false });
    await studio[Symbol.asyncDispose]();
    expect(fakeClient.close).toHaveBeenCalledOnce();
  });

  it("setStudioId and refresh", async () => {
    const client = new FakeStudio();
    const studio = new robloxMod.RobloxStudio(client as unknown as robloxMod.StudioClientLike, "a");

    client.listTools = async () => [new Tool("t1"), new Tool("t2")];
    studio.setStudioId("b");
    expect(studio.studioId).toBe("b");
    const first = await studio.listTools();
    expect(first.length).toBe(2);

    client.listTools = async () => [new Tool("t3")];
    const cached = await studio.listTools();
    expect(cached.length).toBe(2); // cached
    const refreshed = await studio.listTools({ refresh: true });
    expect(refreshed.map((t) => t.name)).toEqual(["t3"]);
  });
});

describe("server disabledTools", () => {
  it("list filters disabled", async () => {
    const fakeClient = {
      protocolVersion: "x",
      capabilities: {},
      serverInfo: {},
      disabledTools: new Set(["secret"]),
      async request() {
        return { tools: [{ name: "ok" }, { name: "secret" }] };
      },
      async close() {},
    };
    const sent: Record<string, unknown>[] = [];
    await handleMessage(fakeClient, { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }, (m) => sent.push(m));
    const names = ((sent[0]["result"] as Record<string, unknown>)["tools"] as Array<Record<string, unknown>>).map(
      (t) => t["name"],
    );
    expect(names).toEqual(["ok"]);
  });

  it("call rejects disabled", async () => {
    const fakeClient = {
      protocolVersion: "x",
      capabilities: {},
      serverInfo: {},
      disabledTools: new Set(["secret"]),
      async request(): Promise<Record<string, unknown>> {
        throw new Error("should not forward");
      },
      async close() {},
    };
    const sent: Record<string, unknown>[] = [];
    await handleMessage(
      fakeClient,
      { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "secret", arguments: {} } },
      (m) => sent.push(m),
    );
    expect("error" in sent[0]).toBe(true);
    expect((sent[0]["error"] as Record<string, unknown>)["code"]).toBe(-32602);
  });
});

describe("insert validation", () => {
  it("missing file raises", async () => {
    const studio = new FakeStudio();
    await expect(extMod.insertAssetFromFile(studio.asStudio(), "/nonexistent/xyz.lua")).rejects.toThrow();
  });

  it("bad parent raises", async () => {
    const studio = new FakeStudio();
    const tmp = path.join(os.tmpdir(), `rbxmcp-${Date.now()}-t.lua`);
    await fs.writeFile(tmp, "print(1)", "utf-8");
    try {
      await expect(extMod.insertAssetFromFile(studio.asStudio(), tmp, { parentPath: "/tmp" })).rejects.toThrow();
    } finally {
      await fs.unlink(tmp).catch(() => undefined);
    }
  });

  it("normalizes CRLF on script insert", async () => {
    const studio = new FakeStudio();
    const tmp = path.join(os.tmpdir(), `rbxmcp-${Date.now()}-crlf.lua`);
    await fs.writeFile(tmp, "print(1)\r\nprint(2)\r\n", "utf-8");
    try {
      await extMod.insertAssetFromFile(studio.asStudio(), tmp, { parentPath: "game.Workspace" });
      const code = studio.executed[0];
      expect(code).not.toContain("\r");
      expect(code).toContain("print(1)\nprint(2)");
    } finally {
      await fs.unlink(tmp).catch(() => undefined);
    }
  });
});

describe("execute from file", () => {
  it("empty file raises", async () => {
    const studio = new FakeStudio();
    const tmp = path.join(os.tmpdir(), `rbxmcp-${Date.now()}-empty.lua`);
    await fs.writeFile(tmp, "   \n", "utf-8");
    try {
      await expect(extMod.executeLuauFromFile(studio.asStudio(), tmp)).rejects.toThrow();
    } finally {
      await fs.unlink(tmp).catch(() => undefined);
    }
  });

  it("normalizes CRLF on execute from file", async () => {
    const studio = new FakeStudio();
    const tmp = path.join(os.tmpdir(), `rbxmcp-${Date.now()}-crlf2.lua`);
    await fs.writeFile(tmp, "return 1\r\n", "utf-8");
    try {
      await extMod.executeLuauFromFile(studio.asStudio(), tmp);
      expect(studio.executed[0]).toBe("return 1\n");
    } finally {
      await fs.unlink(tmp).catch(() => undefined);
    }
  });
});
