/**
 * Unknown tool parameters must be refused, not silently dropped (request P0.2a).
 *
 * Mirrors `python/tests/test_arguments.py`. The two must agree: a call that is
 * rejected on one server and accepted on the other is worse than either, because
 * the behaviour depends on configuration.
 *
 * What is asserted here is the **accept/reject decision**, not the suggestion
 * wording. The two implementations use different similarity metrics - difflib's
 * ratio in Python, a Dice coefficient over bigrams here - so the cutoffs differ
 * (0.5 vs 0.4) while the decision set is the same. Wording is a nicety; the
 * rejection is the contract.
 */
import { describe, expect, it } from "vitest";

import { EXTENDED_TOOLS, rejectUnknownArguments } from "../src/extendedServer.js";
import { INVALID_ARGUMENT, ToolError } from "../src/extended/errors.js";

const declared = (name: string): string[] =>
  Object.keys(EXTENDED_TOOLS.find((t) => t.name === name)?.properties ?? {}).sort();

describe("unknown arguments are refused", () => {
  it("accepts a declared argument", () => {
    expect(() => rejectUnknownArguments("extended_watch_output", { pattern: "^x" })).not.toThrow();
  });

  it("accepts no arguments", () => {
    expect(() => rejectUnknownArguments("extended_watch_output", {})).not.toThrow();
  });

  it("refuses an unknown argument", () => {
    expect(() => rejectUnknownArguments("extended_watch_output", { patttern: "^x" })).toThrow(
      ToolError,
    );
  });

  it("refuses with INVALID_ARGUMENT", () => {
    try {
      rejectUnknownArguments("extended_capture", { format: "png" });
      expect.unreachable("should have thrown");
    } catch (exc) {
      expect(exc).toBeInstanceOf(ToolError);
      expect((exc as ToolError).code).toBe(INVALID_ARGUMENT);
    }
  });

  it("names the tool and the offender", () => {
    try {
      rejectUnknownArguments("extended_capture", { format: "png" });
      expect.unreachable("should have thrown");
    } catch (exc) {
      const message = (exc as ToolError).message;
      expect(message).toContain("extended_capture");
      expect(message).toContain("format");
    }
  });

  it("lists what the tool does accept", () => {
    // A bare "unknown key" sends the caller hunting.
    try {
      rejectUnknownArguments("extended_capture", { nope: 1 });
      expect.unreachable("should have thrown");
    } catch (exc) {
      expect((exc as ToolError).message).toContain("save_path");
    }
  });

  it("suggests the nearest declared name for a near miss", () => {
    try {
      rejectUnknownArguments("extended_capture", { savepath: "/tmp/a.png" });
      expect.unreachable("should have thrown");
    } catch (exc) {
      const message = (exc as ToolError).message;
      expect(message).toContain("did you mean");
      expect(message).toContain("save_path");
    }
  });

  it("offers no suggestion for an unrelated name", () => {
    // A wrong suggestion is worse than none.
    try {
      rejectUnknownArguments("extended_capture", { zzzzqqqxyzzy: 1 });
      expect.unreachable("should have thrown");
    } catch (exc) {
      expect((exc as ToolError).message).not.toContain("did you mean");
    }
  });

  it("reports every unknown argument, not just the first", () => {
    try {
      rejectUnknownArguments("extended_capture", { alpha: 1, beta: 2 });
      expect.unreachable("should have thrown");
    } catch (exc) {
      const message = (exc as ToolError).message;
      expect(message).toContain("alpha");
      expect(message).toContain("beta");
    }
  });

  it("leaves a relayed Studio tool alone", () => {
    // `screen_capture` is relayed, so this project cannot add parameters to it
    // and `format: "png"` is still ignored. Refusing here would break the
    // passthrough. Documented, not fixed - see TODO.md.
    expect(() => rejectUnknownArguments("screen_capture", { format: "png" })).not.toThrow();
  });
});

// A typo'd key that *is* the required key must be reported as a typo.
// Mirrors `UnknownBeatsMissing` in python/tests/test_arguments.py.
//
// Measured live, and the interesting half is what was NOT this project's fault.
// Sending `root_paths` (not `root_path`) came back as "- root_path: Missing
// key" with the typo never named - but that error was raised by the *harness*,
// which validates a JSON Schema `required` list before the request arrives.
// Checked from the other end: `max_resultz`, an unknown *optional* key so it
// survives the harness, does reach the server and is refused by name. This
// project's own client does no schema validation either.
//
// So the server already orders this correctly; the ordering is simply invisible
// through a client that pre-validates. Pinned so it stays that way.
describe("unknown beats missing", () => {
  const TOOL = "extended_script_search_and_read";

  it("calls a typo of a required key a typo", () => {
    try {
      rejectUnknownArguments(TOOL, { root_paths: "game" });
      expect.unreachable("should have thrown");
    } catch (exc) {
      expect((exc as ToolError).message).toContain("root_paths");
    }
  });

  it("accepts a payload that is genuinely missing the required key", () => {
    // The other direction: no typo present, so nothing to name.
    expect(() => rejectUnknownArguments(TOOL, { studio_id: "abc" })).not.toThrow();
  });
});

describe("accept/reject agrees with Python", () => {
  // The contract is the decision, not the message. This is the set that both
  // implementations must agree on; `test_arguments.py` pins the same table.
  const CASES: Array<[string, string[]]> = [
    ["extended_capture", ["format", "savepath", "nope", "path", "zzzzqqqxyzzy"]],
    ["extended_breakpoints", ["line_number", "lines", "scriptpath", "xyzzy", "ln"]],
    ["extended_watch_output", ["patern", "maxlines", "filters", "q"]],
    ["extended_update_script", ["targetpaths", "editss", "skipmissing"]],
  ];

  for (const [tool, keys] of CASES) {
    for (const key of keys) {
      it(`${tool} rejects ${key}`, () => {
        expect(() => rejectUnknownArguments(tool, { [key]: 1 })).toThrow(ToolError);
      });
    }
  }

  for (const tool of ["extended_capture", "extended_breakpoints", "extended_watch_output"]) {
    it(`${tool} accepts every declared argument`, () => {
      const args: Record<string, unknown> = {};
      for (const key of declared(tool)) {
        args[key] = null;
      }
      expect(() => rejectUnknownArguments(tool, args)).not.toThrow();
    });
  }
});
