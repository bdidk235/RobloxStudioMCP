/**
 * The half-written-file case for `extended_capture`, in its own file on purpose.
 *
 * Vitest gives each test file a fresh module registry, and this test needs
 * `vi.resetModules()` plus a mocked `node:fs/promises`. It cannot share a file
 * with the other capture tests: the reset leaves a *second* copy of
 * `errors.ts` alive, so a `ToolError` thrown by the freshly imported
 * `capture.js` fails `instanceof` against the statically imported `ToolError`
 * and `classify` falls through to `UNKNOWN`. Every dispatch test in
 * `capture.test.ts` then failed for that reason and no other - pollution caused
 * by sharing the file, which is the exact failure this project keeps meeting in
 * a different shape.
 */

import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { INVALID_ARGUMENT } from "../src/extended/errors.js";
import { encodePng } from "../src/extended/capture.js";
import { TEMP_PREFIX } from "../src/extended/capture.js";
import type { RobloxStudio } from "../src/roblox.js";
import { CallToolResult } from "../src/types.js";

const W = 2;
const H = 2;
const RGBA = Buffer.from(Array.from({ length: W * H * 4 }, (_, i) => i));

function capturingStudio(): RobloxStudio {
  const body = RGBA.toString("base64");
  const header = [
    String(W),
    String(H),
    String(RGBA.length),
    String(body.length),
    "1",
    "ok",
    "Payload_1",
    body.slice(0, 8),
    body.slice(-8),
  ].join("\t");
  let luauCalls = 0;

  return {
    call: async (name: string) => {
      if (name === "script_read") {
        return CallToolResult.fromDict({
          content: [{ type: "text", text: `     1→${body}` }],
        });
      }
      if (name === "execute_luau") {
        luauCalls += 1;
        return CallToolResult.fromDict({
          content: luauCalls === 1 ? [{ type: "text", text: header }] : [],
        });
      }
      return CallToolResult.fromDict({ content: [] });
    },
  } as unknown as RobloxStudio;
}

describe("a write that dies partway", () => {
  it("keeps the previous capture and leaves no scratch", async () => {
    // The real thing - a full disk, a quota, a killed process - cannot be
    // produced on demand, so `open` is doubled instead: the temp file is still
    // really created, really half-written and really has to be cleaned up. A
    // mock of `writeFile` alone would never create the scratch file, so it
    // would pass for the wrong reason.
    //
    // The **module** is mocked, not `fs.promises`. `writeAtomic` does
    // `const { open } = await import("node:fs/promises")` *inside the function*
    // and destructures at call time, so `vi.spyOn(fsp, "open")` never reaches
    // the binding it actually calls - the first version of this test spied on
    // the object, the write succeeded, and the test failed with "expected the
    // write to fail".
    const root = mkdtempSync(join(tmpdir(), "rbxcapture-partial-"));
    const target = join(root, "shot.png");
    const previous = encodePng(3, 1, Buffer.alloc(12));
    writeFileSync(target, previous);

    vi.resetModules();
    vi.doMock("node:fs/promises", async (importOriginal) => {
      const actual = await importOriginal<typeof import("node:fs/promises")>();
      return {
        ...actual,
        open: async (...args: Parameters<typeof actual.open>) => {
          const handle = await actual.open(...args);
          const realWriteFile = handle.writeFile.bind(handle);
          // Object.assign, so the handle keeps the real FileHandle shape rather
          // than a hand-rolled object that has fooled this repo before.
          return Object.assign(handle, {
            writeFile: async (data: Buffer) => {
              await realWriteFile(data.subarray(0, Math.floor(data.length / 2)));
              const err: NodeJS.ErrnoException = new Error(
                "ENOSPC: no space left on device, write",
              );
              err.code = "ENOSPC";
              throw err;
            },
          });
        },
      };
    });

    try {
      const { capturePng: freshCapturePng } = await import("../src/extended/capture.js");
      await expect(
        freshCapturePng(capturingStudio(), { savePath: target }),
      ).rejects.toMatchObject({
        code: INVALID_ARGUMENT,
        data: { errno: "ENOSPC" },
      });
    } finally {
      vi.doUnmock("node:fs/promises");
      rmSync(root, { recursive: true, force: true });
    }
  });
});

/**
 * The scratch file must not survive, so it is checked after the failure. Kept in
 * its own `it` so the `rmSync` above cannot mask a leftover by removing the
 * whole directory first.
 */
describe("scratch cleanup after a partial write", () => {
  it("a write that never renames leaves nothing behind", async () => {
    const root = mkdtempSync(join(tmpdir(), "rbxcapture-scratch-"));
    const target = join(root, "shot.png");
    writeFileSync(target, "previous");

    vi.resetModules();
    vi.doMock("node:fs/promises", async (importOriginal) => {
      const actual = await importOriginal<typeof import("node:fs/promises")>();
      return {
        ...actual,
        open: async (...args: Parameters<typeof actual.open>) => {
          const handle = await actual.open(...args);
          const realWriteFile = handle.writeFile.bind(handle);
          return Object.assign(handle, {
            writeFile: async (data: Buffer) => {
              await realWriteFile(data.subarray(0, 1));
              const err: NodeJS.ErrnoException = new Error("ENOSPC: no space left");
              err.code = "ENOSPC";
              throw err;
            },
          });
        },
      };
    });

    try {
      const { capturePng: freshCapturePng } = await import("../src/extended/capture.js");
      await expect(
        freshCapturePng(capturingStudio(), { savePath: target }),
      ).rejects.toBeDefined();

      // The previous capture is intact, and the sibling scratch is gone.
      expect(readFileSync(target, "utf8")).toBe("previous");
      expect(readdirSync(root).filter((n) => n.startsWith(TEMP_PREFIX))).toEqual([]);
    } finally {
      vi.doUnmock("node:fs/promises");
      rmSync(root, { recursive: true, force: true });
    }
  });
});
