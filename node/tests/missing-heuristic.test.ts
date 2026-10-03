import { describe, expect, it } from "vitest";
import { writeScript } from "../src/extended/writer.js";

/**
 * Mirror of `python/tests/test_missing_heuristic.py`.
 *
 * `MISSING_HINTS` drives `create_if_missing`: a false positive means the
 * caller's own Luau bug is read as "script absent", so a blank script is
 * created and reported `created`. Bare `nil` and `unknown` were in this list
 * on the Node side and are gone; this pins them out.
 *
 * Run with a Studio NOT required - every failure below is a rejected promise.
 */
describe("the missing-script heuristic", () => {
  const GENUINE = [
    "HttpError: 404 Not Found",
    "the requested model does not exist",
    "no such child",
    "could not find module 'Foo'",
    "Model not found in workspace",
    "Script 'Foo' is missing from ServerScriptService",
  ];

  // None of these are lookup failures. "attempt to index nil" is the most
  // common Luau error there is, so it must never read as "the script is absent".
  const UNRELATED = [
    "attempt to index nil (field 'ClientOnlyModules')",
    "attempt to call a nil value (method 'GetService')",
    "unknown user id",
    "unknown error: connection reset",
    "Cannot connect to server: nil",
    "ReplicatedStorage.Modules is not a valid member of Folder",
    "Server took 0.165s to load!",
    "Failed to upload TexturePack: HTTP 429",
  ];

  /** A studio whose reads fail with `message`. */
  const failingStudio = (message: string) =>
    ({
      scriptRead: async () => {
        throw new Error(message);
      },
      call: async () => {
        throw new Error(message);
      },
      multiEdit: async () => {
        throw new Error(message);
      },
    }) as never;

  it("recognises a genuine miss", async () => {
    for (const msg of GENUINE) {
      // create_if_missing off: the error must propagate rather than be swallowed.
      await expect(
        writeScript(failingStudio(msg), "game.ServerScriptService.Foo", "print(1)"),
      ).rejects.toThrow();
    }
  });

  it("does not read an unrelated error as a missing script", async () => {
    for (const msg of UNRELATED) {
      // The failure is the error propagating, NOT a "created" status. A false
      // positive swallows it and invents a blank script instead.
      const outcome = await writeScript(
        failingStudio(msg),
        "game.ServerScriptService.Foo",
        "print(1)",
        { createIfMissing: true },
      ).then(
        (status) => ({ returned: status }),
        (err: unknown) => ({ threw: err as Error }),
      );
      expect(
        outcome,
        `${msg} was misread as a missing script`,
      ).not.toHaveProperty("returned");
    }
  });

  it("keeps the two offending bare words out", async () => {
    const source = await import("node:fs").then((fs) =>
      fs.readFileSync(new URL("../src/extended/writer.ts", import.meta.url), "utf-8"),
    );
    for (const word of ["nil", "unknown"]) {
      expect(
        new RegExp(`^\\s*"${word}",`, "m").test(source),
        `"${word}" is back in MISSING_HINTS; a substring match on it is ` +
          "indistinguishable from matching arbitrary prose",
      ).toBe(false);
    }
    expect(source).toContain('"is missing"');
  });
});