/**
 * End-to-end `extendedScriptGrep` over a simulated relay, no Studio needed.
 *
 * Mirrors `python/tests/test_grep_end_to_end.py`. The bug had two halves, and
 * only one was in the parser:
 *
 * 1. `_parse_grep_raw` attempted JSON only, while the relay returns **plain
 *    text** - so the tool returned an empty array with a success status for text
 *    that provably existed. On Python that was worse: with `root_path` it raised
 *    an unhandled `AttributeError` that escaped the tool.
 * 2. The `string[]` fallback reached `hit.get("path")` in Python. JavaScript does
 *    not throw there - `("abc")["path"]` is `undefined` - so Node **silently
 *    dropped every real hit** instead, which is the same wrong answer by a
 *    quieter route.
 *
 * Every fixture is captured from a live call, not hand-written. The old tests
 * mocked the old JSON shape, which is why the suite stayed green through a format
 * change on someone else's side.
 *
 * Live verification needs an MCP server restart, because the running process
 * holds the old module. These tests prove the code, not the deployment.
 */
import { describe, expect, it } from "vitest";

import { extendedScriptGrep, type GrepHit } from "../src/extended/grep.js";

/** Captured from a live `script_grep(query="print")` on Studio 0_186489. */
const LIVE_TEXT_REPLY =
  "Path: game.ServerScriptService.UsabilityProbe | Line: 2 | print(42)\n" +
  'Path: game.ServerScriptService.Helper | Line: 10 | print("x")\n' +
  "... Search stopped after reaching the limit of 50 matches.";

const JSON_ARRAY_REPLY =
  '[{"path": "game.ServerScriptService.A", "line_number": 4, "content": "print(1)"}]';

class FakeStudio {
  reads: string[] = [];
  constructor(
    private readonly reply: string,
    private readonly source = "print(42)\nlocal x = 1\nprint(x)\n",
  ) {}
  async call(tool: string): Promise<{ text(): string }> {
    if (tool !== "script_grep") throw new Error("unexpected tool " + tool);
    return { text: () => this.reply };
  }
  async scriptRead(targetFile: string): Promise<{ text(): string }> {
    this.reads.push(targetFile);
    return { text: () => this.source };
  }
}

const run = async (reply: string, options = {}): Promise<[GrepHit[], FakeStudio]> => {
  const studio = new FakeStudio(reply);
  return [await extendedScriptGrep(studio as never, "print", options), studio];
};

describe("live text reply", () => {
  it("returns hits rather than an empty array", async () => {
    // The regression. This exact reply produced [] with a success status.
    const [hits] = await run(LIVE_TEXT_REPLY);
    expect(hits).toHaveLength(2);
  });

  it("carries path and line number through", async () => {
    const [hits] = await run(LIVE_TEXT_REPLY);
    expect(hits[0]!.path).toBe("game.ServerScriptService.UsabilityProbe");
    expect(hits[0]!.line_number).toBe(2);
    expect(hits[1]!.line_number).toBe(10);
  });

  it("does not report the truncation notice as a hit", async () => {
    // It reads as a hit if you do not exclude it, and would report a file named
    // after an English sentence.
    const [hits] = await run(LIVE_TEXT_REPLY);
    expect(hits.some((h) => h.path.includes("Search stopped"))).toBe(false);
  });

  it("emits exactly the four output keys", async () => {
    const [hits] = await run(LIVE_TEXT_REPLY);
    for (const hit of hits) {
      expect(Object.keys(hit).sort()).toEqual([
        "context_lines", "excerpt", "line_number", "path",
      ]);
    }
  });

  it("does not read the source when the reply supplied content", async () => {
    // A source read here is wasted work and a wasted turn.
    const [hits, studio] = await run(LIVE_TEXT_REPLY);
    expect(hits[0]!.excerpt).toContain("print(42)");
    expect(studio.reads).toEqual([]);
  });
});

describe("no crash or silent drop on any shape", () => {
  it("a JSON array of strings yields nothing rather than throwing", async () => {
    // This is the exact input that produced
    // `'str' object has no attribute 'get'` on Python. In JavaScript it would
    // have silently dropped every hit instead, because `("abc")["path"]` is
    // undefined rather than a throw - the same wrong answer, quieter.
    const [hits] = await run('["not", "a", "dict"]');
    expect(hits).toEqual([]);
  });

  it("mixed junk is dropped and real hits survive", async () => {
    const reply = '["junk", {"path": "game.S", "line_number": 2, "content": "print(1)"}]';
    const [hits] = await run(reply);
    expect(hits.map((h) => h.path)).toEqual(["game.S"]);
  });

  it("unparseable input is empty, not an exception", async () => {
    const [hits] = await run("something unexpected entirely");
    expect(hits).toEqual([]);
  });
});

describe("JSON still works", () => {
  it("a JSON array reply is unchanged", async () => {
    // The text branch must not shadow the formats that already worked.
    const [hits] = await run(JSON_ARRAY_REPLY);
    expect(hits.map((h) => h.path)).toEqual(["game.ServerScriptService.A"]);
    expect(hits[0]!.line_number).toBe(4);
  });

  it("reads the source when the reply omits content", async () => {
    // The enrichment fallback, which is the whole point of this tool over raw
    // `script_grep`: context lines the relay did not send.
    const reply = '[{"path": "game.ServerScriptService.A", "line_number": 2}]';
    const [hits, studio] = await run(reply, { contextLines: 1 });
    expect(studio.reads).toEqual(["game.ServerScriptService.A"]);
    expect(hits[0]!.context_lines).toBe(1);
    // contextLines=1 is a radius, not a total: line 2 of 3 yields all three.
    expect(hits[0]!.excerpt).toBe("print(42)\nlocal x = 1\nprint(x)");
  });
});
