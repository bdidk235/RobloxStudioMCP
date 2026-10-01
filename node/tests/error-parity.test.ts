/**
 * The two servers must emit the same error payload for the same request.
 *
 * Mirrors `python/tests/test_error_parity.py`. Both read `parity/errors.json`.
 *
 * **Why this file exists.** Three measured defects, none of which either side's
 * own tests could have seen, because each only ever looked at its own output.
 *
 * *Shape.* Python nested the tool-specific keys under `data.details`; Node
 * spread them flat, and `rejectUnknownArguments` attached no `data` at all. A
 * caller branching on `data.unknown` worked on Node and read `undefined` on
 * Python - silently, and on exactly the branch written to handle the error.
 *
 * *Value rendering.* Python's `repr` writes `None`/`True`/`'x'` where this writes
 * `null`/`true`/`"x"`, and `json.dumps`'s default separators differ from
 * `JSON.stringify`'s. Same request, two different error strings; an agent that
 * string-matches - which is what an agent does - has to handle both. Both sides
 * now render through a shared `describe`.
 *
 * *Accept/reject.* `updateScript` cast a malformed edit to a `Record` and read
 * `undefined` out of it, so `[42]` was rejected on Python and silently accepted
 * here.
 *
 * So the assertions are behavioural: every probe goes through the **real code** on
 * both sides, and both must produce the same code and the same message. Reading a
 * code off `classify()` for a hand-written string would test the classifier
 * rather than the raise site, which is exactly what the `ToolError` raise sites
 * exist to stop depending on.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, beforeAll, afterAll } from "vitest";

import {
  EXTENDED_TOOLS,
  EXTENDED_HANDLERS,
  rejectUnknownArguments,
  toJsonRpcError,
} from "../src/extendedServer.js";
import { ALL_CODES, classify, ToolError } from "../src/extended/errors.js";
import { setBreakpoint } from "../src/extended/breakpoints.js";
import { encodePng, parseHeader, capturePng } from "../src/extended/capture.js";
import { insertAssetFromFile } from "../src/extended/extensions.js";
import { extendedScriptGrep } from "../src/extended/grep.js";
import { updateScript } from "../src/extended/updater.js";
import { writeScript } from "../src/extended/writer.js";
import type { RobloxStudio } from "../src/roblox.js";
// A value import, not `import type`: this fake builds real CallToolResults through
// `fromDict`, the same constructor the client uses, so it cannot drift from the
// real shape the way a hand-rolled object can.
import { CallToolResult } from "../src/types.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");

interface UnknownArgumentCase {
  tool: string;
  args: Record<string, unknown>;
  code: string;
  data: Record<string, unknown>;
}
interface HandlerCase {
  name: string;
  tool: string;
  args: Record<string, unknown>;
  code: string;
  message: string;
  data?: Record<string, unknown>;
  why?: string;
}
interface SiteCase {
  site: string;
  code: string;
  message: string;
  code_only?: boolean;
  why?: string;
}
interface SiteOnlyCase {
  site: string;
  code: string;
  message: string;
  /**
   * Present-but-optional so it can be read off the union without narrowing.
   * `sites` is typed as `SiteCase | SiteOnlyCase`, and the test below reads
   * `testCase.code_only` on every member, so a member lacking the key is a
   * compile error. Caught by `tsconfig.check.json`, which type-checks test files
   * where the default `tsc --noEmit` does not.
   */
  code_only?: boolean;
  why: string;
}
interface Contract {
  unknown_arguments: UnknownArgumentCase[];
  handlers: HandlerCase[];
  sites: Array<SiteCase | SiteOnlyCase>;
  deliberately_not_argument_faults: Array<{ site: string; message: string; code: string; why: string }>;
}

const CONTRACT: Contract = JSON.parse(
  readFileSync(resolve(REPO_ROOT, "parity", "errors.json"), "utf8"),
);

/**
 * Just enough of a Studio for the library layers to reach their own guards.
 *
 * Every probe fails *before* it would call anything here, so a probe that
 * started reaching for this object would be a probe that had stopped testing
 * validation.
 */
function fakeStudio(files: Record<string, string> = {}): RobloxStudio {
  return {
    scriptRead: async (p: string) => {
      if (!(p in files)) throw new Error("could not find any instances");
      return { text: () => files[p]! } as CallToolResult;
    },
    call: async () => ({ text: () => "" }) as unknown as CallToolResult,
  } as unknown as RobloxStudio;
}

let scratch = "";

beforeAll(() => {
  scratch = mkdtempSync(join(tmpdir(), "err-parity-"));
  writeFileSync(join(scratch, "real.lua"), "print(1)");
});
afterAll(() => rmSync(scratch, { recursive: true, force: true }));

/** The code and message a caller would actually receive. */
async function attempt(fn: () => unknown | Promise<unknown>): Promise<[string, string]> {
  try {
    const result = await fn();
    throw new Error(`expected a failure, got ${JSON.stringify(result)}`);
  } catch (exc) {
    if (exc instanceof Error && exc.message.startsWith("expected a failure")) throw exc;
    const err = classify(exc);
    return [err.code, err.message];
  }
}

// One probe per contract entry, so the contract drives the test rather than the
// other way round.
const SITES: Record<string, () => unknown | Promise<unknown>> = {
  "breakpoints: line 0": () => setBreakpoint(fakeStudio(), "BpTest", 0),
  "grep: query empty": () => extendedScriptGrep(fakeStudio(), ""),
  "grep: max_results 0": () => extendedScriptGrep(fakeStudio(), "x", { maxResults: 0 }),
  "grep: max_results 'x'": () =>
    extendedScriptGrep(fakeStudio(), "x", { maxResults: "x" as unknown as number }),
  "grep: context_lines 11": () => extendedScriptGrep(fakeStudio(), "x", { contextLines: 11 }),
  // The tail is the regex engine's own wording, so the contract pins only the code.
  "grep: bad regex": () => extendedScriptGrep(fakeStudio(), "([a", { regex: true }),
  "write: bad className": () =>
    writeScript(fakeStudio({ "game.S.A": "hi" }), "game.S.A", "x", { className: "Part" }),
  "write: non-game path": () => writeScript(fakeStudio(), "/tmp/x.lua", "hi"),
  "update: non-game path": () => updateScript(fakeStudio(), "/tmp/x.lua", [["a", "b"]]),
  "update: no match": () =>
    updateScript(fakeStudio({ "game.S.A": "hello" }), "game.S.A", [["nope", "x"]], {
      skipMissing: false,
    }),
  "update: ambiguous": () =>
    updateScript(fakeStudio({ "game.S.A": "foo foo" }), "game.S.A", [["foo", "bar"]], {
      skipMissing: false,
    }),
  "update: noop": () =>
    updateScript(fakeStudio({ "game.S.A": "hi" }), "game.S.A", [["hi", "hi"]], { skipNoOps: false }),
  "update: empty old_string": () =>
    updateScript(fakeStudio({ "game.S.A": "hi" }), "game.S.A", [["", "x"]], {
      skipMissing: false,
    }),
  "update: bad edit shape": () =>
    updateScript(fakeStudio({ "game.S.A": "hi" }), "game.S.A", [42 as unknown as [string, string]]),
  "insert: missing file": () => insertAssetFromFile(fakeStudio(), "C:\\nope.luau"),
  // Needs a real file: a missing one is refused first, for a different reason.
  "insert: bad parent": () =>
    insertAssetFromFile(fakeStudio(), join(scratch, "real.lua"), { parentPath: "/tmp" }),
  "capture: rgba length mismatch": () => encodePng(4, 4, Buffer.alloc(10)),
  "capture: short header": () => parseHeader("only\ttwo\tfields"),
  "capture: studio bad allocation": () =>
    parseHeader(["1", "1", "4", "8", "1", "bad allocation", "P", "a", "b"].join("\t")),
  "save_path: unwritable destination": () => captureBadSavePath(),
};

/**
 * A capture that succeeds and then cannot be written.
 *
 * Needs a Studio double that actually completes a capture - `fakeStudio` is built
 * for probes that fail *before* they call anything, so reusing it here would make
 * this probe pass for the wrong reason (it would fault on the capture, not on the
 * write) and the declared code would never be reached.
 */
async function captureBadSavePath(): Promise<unknown> {
  try {
    return await capturePng(capturingStudio(), {
      savePath: join(scratch, "no-such-dir", "shot.png"),
    });
  } finally {
    rmSync(join(scratch, "no-such-dir"), { recursive: true, force: true });
  }
}

/**
 * The smallest Studio that survives `captureRgba` end to end.
 *
 * Every field `parseHeader` reads is derived from the payload rather than typed
 * in, so a change to the encoding cannot leave the fake quietly wrong. The
 * `CallToolResult` is built through `fromDict`, not as a `{ text }` object: the
 * real one is constructed that way, and a hand-rolled double has already been
 * wrong in the same way on both sides of this repo once.
 */
function capturingStudio(): RobloxStudio {
  const rgba = Buffer.from(Array.from({ length: 2 * 2 * 4 }, (_, i) => i));
  const body = rgba.toString("base64");
  const header = [
    "2",
    "2",
    String(rgba.length),
    String(body.length),
    "1",
    "ok",
    "Payload_1",
    body.slice(0, 8),
    body.slice(-8),
  ].join("\t");

  return {
    call: async (name: string, args: Record<string, unknown> = {}) => {
      if (name === "execute_luau") {
        const code = String(args["code"] ?? "");
        if (code.includes("CaptureScreenshot")) {
          return CallToolResult.fromDict({ content: [{ type: "text", text: header }] });
        }
        if (code.includes("if m then m:Destroy() end")) {
          return CallToolResult.fromDict({ content: [] });
        }
        // Raising beats returning "": an empty header faults in `parseHeader`, so
        // the probe would pass without ever reaching the write it is meant to be
        // pinning.
        //
        // Discriminated on content, not on call order. `RBXCapture`,
        // `FindFirstChild` and `Destroy()` all appear in *both* snippets, so a
        // guess on any of them returns "" for the header - which is exactly what
        // happened here first time.
        throw new Error("unrecognised execute_luau call");
      }
      if (name === "script_read") {
        // script_read's own line prefix, which the stripper must remove.
        return CallToolResult.fromDict({ content: [{ type: "text", text: `     1→${body}` }] });
      }
      return CallToolResult.fromDict({ content: [] });
    },
  } as unknown as RobloxStudio;
}

function viaReject(tool: string, args: Record<string, unknown>): ToolError {
  try {
    rejectUnknownArguments(tool, args);
  } catch (exc) {
    if (exc instanceof ToolError) return exc;
    throw exc;
  }
  throw new Error(`${tool} accepted ${JSON.stringify(args)}, which the contract says it must not`);
}

async function viaHandler(tool: string, args: Record<string, unknown>): Promise<ToolError> {
  try {
    await EXTENDED_HANDLERS[tool]!(null as never, args);
  } catch (exc) {
    return classify(exc);
  }
  throw new Error(`${tool} accepted ${JSON.stringify(args)}, which the contract says it must not`);
}

describe("unknown-argument payloads match the contract", () => {
  it("is not empty", () => {
    expect(CONTRACT.unknown_arguments.length).toBeGreaterThanOrEqual(4);
  });

  for (const testCase of CONTRACT.unknown_arguments) {
    it(`${testCase.tool} rejects ${Object.keys(testCase.args).join(", ")} with the declared data`, () => {
      const err = viaReject(testCase.tool, testCase.args);
      const expected = { ...testCase.data };
      const wireCode = expected["wire_data_code"];
      delete expected["wire_data_code"];
      const payload = err.toError();
      expect(payload["code"]).toBe(wireCode);
      expect(payload["data"]).toEqual(expected);
    });
  }

  it("data is flat, not nested", () => {
    // The specific regression, pinned independently of the table. A fixture
    // that merely happens to be flat today would not stop someone
    // re-introducing the nesting while also updating the fixture.
    const data = viaReject("extended_capture", { format: "png" }).toError()["data"] as Record<
      string,
      unknown
    >;
    expect(data["details"]).toBeUndefined();
    expect(data["tool"]).toBe("extended_capture");
    expect(data["unknown"]).toEqual(["format"]);
    expect(data["accepted"]).toEqual(["save_path", "studio_id"]);
  });

  it("the wire payload carries the code inside data", () => {
    // What the relay loop actually sends. `ToolError.toError()` keeps the code
    // at the top level; the relay builder adds it to `data` too, so a caller
    // reading either gets it. `test_error_parity.py` asserts the same.
    const data = toJsonRpcError(viaReject("extended_capture", { format: "png" }))[
      "data"
    ] as Record<string, unknown>;
    expect(data["code"]).toBe("INVALID_ARGUMENT");
    expect(data["details"]).toBeUndefined();
    expect(data["unknown"]).toEqual(["format"]);
  });

  it("the data payload is JSON serialisable", () => {
    for (const testCase of CONTRACT.unknown_arguments) {
      expect(() => JSON.stringify(viaReject(testCase.tool, testCase.args).toError())).not.toThrow();
    }
  });
});

describe("handler faults carry a code", () => {
  // Driven through the real handlers, not through hand-written strings.
  // `classify` is the wrong lens here: twenty-two of roughly twenty-seven caller
  // faults were bare `Error` throws that no pattern matched, and the fix was to
  // throw `ToolError` at the raise site. Asserting here therefore tests the
  // raise sites, which is where a regression would actually appear.
  it("the contract is not empty", () => {
    expect(CONTRACT.handlers.length).toBeGreaterThanOrEqual(12);
  });

  for (const testCase of CONTRACT.handlers) {
    it(`${testCase.name} -> ${testCase.code}`, async () => {
      const err = await viaHandler(testCase.tool, testCase.args);
      expect(err.code).toBe(testCase.code);
      // Not a wording preference: the two sides used to say different things for
      // the same request. An agent branches on the code and *reads* the message,
      // so divergent wording makes the parity claim false.
      expect(err.message).toBe(testCase.message);
      if (testCase.data !== undefined) {
        expect(err.data).toEqual(testCase.data);
      }
    });
  }

  it("no caller fault falls through to UNKNOWN", () => {
    // The bug this whole change exists to fix. Asserted as a property so
    // updating the table to hide a regression does not update this check too.
    for (const testCase of CONTRACT.handlers) {
      expect(testCase.code).not.toBe("UNKNOWN");
    }
  });
});

describe("library site faults carry a code", () => {
  // The library layers are public entry points in their own right, so validating
  // only at the handler would leave a hole.
  it("the contract is not empty", () => {
    expect(CONTRACT.sites.length).toBeGreaterThanOrEqual(15);
  });

  it("every site in the contract is driven", () => {
    // A contract entry nothing exercises is a comment that looks like a test.
    for (const testCase of CONTRACT.sites) {
      expect(SITES[testCase.site], `no probe for ${testCase.site}`).toBeTypeOf("function");
    }
  });

  it("every probe is in the contract", () => {
    for (const name of Object.keys(SITES)) {
      expect(CONTRACT.sites.map((s) => s.site), `probe ${name} is not pinned`).toContain(name);
    }
  });

  for (const testCase of CONTRACT.sites) {
    it(`${testCase.site} -> ${testCase.code}`, async () => {
      const [code, message] = await attempt(SITES[testCase.site]!);
      expect(code).toBe(testCase.code);
      if (!testCase.code_only) {
        expect(message).toBe(testCase.message);
      }
    });
  }

  it("a conversion decision records why", () => {
    for (const testCase of CONTRACT.sites) {
      if (testCase.code === "INVALID_ARGUMENT") {
        expect(
          (testCase as { why?: string }).why ?? testCase.message,
          `${testCase.site} is INVALID_ARGUMENT with no recorded reason`,
        ).toBeTruthy();
      }
    }
  });

  it("the integrity checks stay off INVALID_ARGUMENT", () => {
    // The capture sites check *our* consistency, not the caller's request.
    // INVALID_ARGUMENT would send the caller off to edit a request that was
    // fine, which is worse than an honest UNKNOWN.
    for (const testCase of CONTRACT.sites) {
      if (testCase.site.startsWith("capture:")) {
        expect(testCase.code, testCase.site).not.toBe("INVALID_ARGUMENT");
      }
    }
  });

  it("a text edit miss is not NOT_FOUND", () => {
    // The one most likely to be "corrected" back. NOT_FOUND's recovery is to
    // re-list the DataModel, which cannot work for an edit whose target was just
    // read successfully.
    return attempt(SITES["update: no match"]!).then(([code]) => {
      expect(code).toBe("INVALID_ARGUMENT");
      expect(code).not.toBe("NOT_FOUND");
    });
  });

  it("a host file miss is not NOT_FOUND", () => {
    return attempt(SITES["insert: missing file"]!).then(([code]) => {
      expect(code).toBe("INVALID_ARGUMENT");
      expect(code).not.toBe("NOT_FOUND");
    });
  });
});

describe("deliberate non-argument faults", () => {
  // Codes chosen *against* the grain, recorded so they are not "fixed". Each is
  // a site that looks like a caller fault and is not, or is a caller fault a
  // looser code would have swept up. Converting them mechanically would tell the
  // caller to do something that cannot help.
  for (const testCase of CONTRACT.deliberately_not_argument_faults) {
    it(`${testCase.site} stays on ${testCase.code}`, () => {
      expect(classify(new Error(testCase.message)).code).toBe(testCase.code);
    });
  }

  it("each records why", () => {
    // A decision without a recorded reason gets reversed by the next reader.
    for (const testCase of CONTRACT.deliberately_not_argument_faults) {
      expect(testCase.why, testCase.site).toBeTruthy();
    }
  });
});

describe("the vocabulary and the accepted action set", () => {
  it("every declared code is in the vocabulary", () => {
    const declared = [
      ...CONTRACT.handlers,
      ...CONTRACT.sites,
      ...CONTRACT.deliberately_not_argument_faults,
    ];
    for (const testCase of declared) {
      const label = (testCase as { name?: string }).name ?? (testCase as { site: string }).site;
      expect(ALL_CODES.has(testCase.code), label).toBe(true);
    }
  });

  it("the action set is not hand-written", () => {
    // The unknown-action error is the one an agent is guaranteed to hit, and the
    // accepted set is the whole point of it. It is read from the schema on both
    // sides rather than spelled out, so adding an action cannot leave the error
    // advertising the old list.
    const tool = EXTENDED_TOOLS.find((t) => t.name === "extended_manage_instance");
    const actions = (tool?.properties?.["action"] as { enum?: string[] } | undefined)?.enum;
    expect(actions).toEqual(["list", "launch", "stop", "places", "make_place"]);
  });
});

describe("recovery wording", () => {
  // The DATAMODAL_UNAVAILABLE hint, pinned against its measured failure. Found
  // by hostile fuzzing: breakpoints in Edit mode with NO session running got
  // "Server datamodel is not available in Edit mode" plus a hint written for
  // the opposite case - asserting a session is running and recommending
  // action='stop', which terminates the process rather than ending a session.
  // Mirrors `python/tests/test_error_parity.py::RecoveryWording`.
  const recovered = (): string =>
    classify(new Error("Server datamodel is not available in Edit mode")).message;

  it("does not assert a session is running", () => {
    expect(recovered()).not.toContain("while a play session is running");
  });

  it("covers both directions", () => {
    expect(recovered()).toContain("Edit mode");
    expect(recovered()).toContain("play session");
  });

  it("never presents stop as session control", () => {
    expect(recovered()).not.toContain("stop it first");
    expect(recovered()).toContain("terminates the Studio");
  });

  it("keeps the engine message first", () => {
    expect(recovered().startsWith("Server datamodel is not available in Edit mode")).toBe(true);
  });
});
