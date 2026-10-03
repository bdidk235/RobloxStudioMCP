/**
 * Error codes and `watch_output` parity, mirroring
 * `python/tests/test_watch_output.py`.
 *
 * Both files exist because the two implementations drifted. Node's
 * `extended_watch_output` accepted only `studio_id` - no `pattern`, no
 * `max_lines` - and because unknown parameters are silently ignored (request
 * P0.2a) a caller sending `pattern` on the Node side got the **whole buffer**
 * back with no error. That reads exactly like "no breakpoint was hit", which is
 * the specific failure the `rsx-breakpoints` skill's `pattern='^Breakpoint '`
 * recipe is meant to prevent.
 *
 * The Python side had the mirror-image bug: the handler read `result["text"]`
 * while the watch returns `new_lines`, so it returned `{"returned": 0}` on every
 * call and reported success.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import {
  ALL_CODES,
  AMBIGUOUS_STUDIO,
  CAPABILITY_DENIED,
  DATAMODAL_UNAVAILABLE,
  INVALID_ARGUMENT,
  LUA_ERROR,
  NO_STUDIO,
  NOT_FOUND,
  PLACE_NOT_OPEN,
  LAUNCH_FAILED,
  SIZE_LIMIT,
  STALE_STUDIO_ID,
  TEST_BUSY,
  TEST_REFUSED,
  TIMEOUT,
  ToolError,
  UNKNOWN,
  classify,
  isLuaFault,
} from "../src/extended/errors.js";

/** This file's directory, for the source scan in `raisedCodes` below. */
const HERE = dirname(fileURLToPath(import.meta.url));

describe("ToolError", () => {
  it("keeps a stable code and structured data", () => {
    const err = new ToolError(INVALID_ARGUMENT, "bad pattern", { pattern: "([a" });
    expect(err.code).toBe("INVALID_ARGUMENT");
    expect(err.data).toEqual({ pattern: "([a" });
  });

  it("rejects a code outside the vocabulary", () => {
    // A typo'd code would otherwise ship and never match a caller's switch.
    // This is also the negative control for the test below: deleting the
    // validation to stop it swallowing messages would fail here.
    expect(() => new ToolError("NOT_A_CODE", "x")).toThrow(/unknown error code/);
  });

  it("reports the message when the code is outside the vocabulary", () => {
    // The guard must refuse the code without discarding the error: the
    // message is the part a caller can act on, so it has to survive.
    expect(() => new ToolError("NOT_A_CODE", "the pixels are fine, the write failed"))
      .toThrow(/the pixels are fine, the write failed/);
    expect(() => new ToolError("NOT_A_CODE", "the pixels are fine, the write failed"))
      .toThrow(/NOT_A_CODE/);
  });

  it("leaves a declared code untouched", () => {
    for (const code of ALL_CODES) {
      const err = new ToolError(code, `message for ${code}`);
      expect(err.code).toBe(code);
      expect(err.message).toBe(`message for ${code}`);
    }
  });

  it("survives instanceof after subclassing", () => {
    // Downlevelled ES5 subclassing breaks this without setPrototypeOf.
    const err = new ToolError(TIMEOUT, "slow");
    expect(err instanceof ToolError).toBe(true);
    expect(err instanceof Error).toBe(true);
  });

  it("omits an empty data object from the JSON-RPC payload", () => {
    expect(new ToolError(TIMEOUT, "slow").toError()).toEqual({
      code: TIMEOUT,
      message: "slow",
    });
  });

  it("includes data when present", () => {
    expect(new ToolError(NO_STUDIO, "none").toError()["data"]).toBeUndefined();
    expect(new ToolError(NO_STUDIO, "none", { n: 2 }).toError()["data"]).toEqual({ n: 2 });
  });
});

describe("classify", () => {
  it("passes a ToolError through unchanged", () => {
    const original = new ToolError(TEST_BUSY, "already running");
    expect(classify(original)).toBe(original);
  });

  it("detects a Luau fault before the message table", () => {
    // The ordering is load-bearing: "Failed to parse command code" contains
    // "parse", and a syntax error in the caller's own Luau is a different class
    // of problem from the engine refusing a request.
    const err = classify(new Error("Failed to parse command code"));
    expect(err.code).toBe(LUA_ERROR);
    expect(err.data["classified_from"]).toBe("luau-fault-pattern");
  });

  it("classifies a disconnected studio", () => {
    expect(classify(new Error("no Roblox Studio instances are connected")).code).toBe(NO_STUDIO);
  });

  it("does not let a loose pattern swallow a specific one", () => {
    // "not connected" would otherwise claim the ambiguity error.
    expect(classify(new Error("more than one studio is connected")).code).toBe(AMBIGUOUS_STUDIO);
  });

  it("maps a stale studio id", () => {
    expect(classify(new Error("requested studio_id is not connected")).code).toBe(STALE_STUDIO_ID);
  });

  it("maps capability refusals", () => {
    expect(classify(new Error("not permitted")).code).toBe(CAPABILITY_DENIED);
    expect(classify(new Error("requires the RobloxScript")).code).toBe(CAPABILITY_DENIED);
  });

  it("maps limits, timeouts, refusals and lookups", () => {
    expect(classify(new Error("bad allocation")).code).toBe(SIZE_LIMIT);
    expect(classify(new Error("operation timed out")).code).toBe(TIMEOUT);
    expect(classify(new Error("AddPlayers refused")).code).toBe(TEST_REFUSED);
    expect(classify(new Error("could not find any instances")).code).toBe(NOT_FOUND);
    expect(classify(new Error("datamodel is not available")).code).toBe(DATAMODAL_UNAVAILABLE);
    expect(classify(new Error("a previous one is still in progress")).code).toBe(TEST_BUSY);
  });

  it("falls back to UNKNOWN rather than guessing", () => {
    expect(classify(new Error("something entirely new")).code).toBe(UNKNOWN);
  });

  it("classifies a non-Error throw", () => {
    expect(classify("a bare string").code).toBe(UNKNOWN);
  });
});

describe("isLuaFault", () => {
  it("recognises runtime faults", () => {
    expect(isLuaFault("attempt to index nil")).toBe(true);
    expect(isLuaFault("unbalanced 'end'")).toBe(true);
  });

  it("does not claim an API refusal", () => {
    expect(isLuaFault("not permitted")).toBe(false);
  });
});

describe("code vocabulary", () => {
  it("matches the Python set", () => {
    // If one side gains a code the other must too, or a caller switching
    // implementations cannot rely on the switch being exhaustive.
    const expected = [
      NO_STUDIO, STALE_STUDIO_ID, AMBIGUOUS_STUDIO, DATAMODAL_UNAVAILABLE,
      PLACE_NOT_OPEN, NOT_FOUND, SIZE_LIMIT, TEST_BUSY, TEST_REFUSED,
      LAUNCH_FAILED, TIMEOUT, INVALID_ARGUMENT, LUA_ERROR, CAPABILITY_DENIED,
      UNKNOWN,
    ];
    expect(ALL_CODES.size).toBe(expected.length);
    for (const code of expected) {
      expect(ALL_CODES.has(code)).toBe(true);
    }
  });
});

describe("every raised code is declared", () => {
  /**
   * The direction the vocabulary test above does not check, and the mirror of
   * `python/tests/test_closed_sets.py::EveryRaisedCodeIsDeclared`.
   *
   * `code vocabulary` proves every *declared* code is well-formed. It said
   * nothing about raised codes, and `extended_capture` shipped an
   * `INTERNAL_ERROR` that is not declared - so `ToolError`'s constructor threw
   * while constructing the error and replaced it. The caller received
   * `UNKNOWN: unknown error code "INTERNAL_ERROR"`: no code, no metadata, and the
   * message saying the capture had succeeded destroyed.
   *
   * A published code that cannot occur is worse than a missing one. A *raised*
   * code that is not published is the same defect from the other side, and it is
   * strictly more damaging, because it does not merely fail to match - it throws
   * away the error it was carrying.
   */
  function raisedCodes(): Array<[string, string]> {
    const src = resolve(HERE, "..", "src", "extended");
    const out: Array<[string, string]> = [];
    const pattern = /new ToolError\(\s*([A-Z_]+|"[A-Z_]+")/g;
    for (const name of readdirSync(src)) {
      if (!name.endsWith(".ts")) continue;
      const text = readFileSync(join(src, name), "utf8");
      for (const match of text.matchAll(pattern)) {
        out.push([name, match[1]!.replace(/"/g, "")]);
      }
    }
    return out;
  }

  it("no raise site names an undeclared code", () => {
    const offenders = raisedCodes().filter(([, code]) => !ALL_CODES.has(code));
    expect(
      offenders.map(([name, code]) => `${name}: ${code}`),
      "these raise a code that is not in ALL_CODES, so ToolError throws instead " +
        "and the error they built is discarded",
    ).toEqual([]);
  });

  it("the scan actually finds raise sites", () => {
    // A scan that matches nothing passes vacuously and looks like coverage.
    const found = new Set(raisedCodes().map(([, code]) => code));
    expect(found.has(INVALID_ARGUMENT)).toBe(true);
    expect(found.size).toBeGreaterThanOrEqual(3);
  });
});
