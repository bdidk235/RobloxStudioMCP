import { inflateSync } from "node:zlib";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  MAX_BASE64_CHARS,
  TEMP_PREFIX,
  capturePng,
  crc32,
  encodePng,
  parseHeader,
  stripLinePrefixes,
} from "../src/extended/capture.js";
import { ALL_CODES, INVALID_ARGUMENT, ToolError, describe as describeValue } from "../src/extended/errors.js";
import { handleExtendedMessage } from "../src/extendedServer.js";
import type { RobloxStudio } from "../src/roblox.js";
import type { MCPClient } from "../src/client.js";
import { CallToolResult } from "../src/types.js";

interface PngChunk {
  tag: string;
  data: Buffer;
  crcOk: boolean;
}

function chunksOf(png: Buffer): PngChunk[] {
  const out: PngChunk[] = [];
  let pos = 8;
  while (pos < png.length) {
    const length = png.readUInt32BE(pos);
    const tag = png.toString("latin1", pos + 4, pos + 8);
    const data = png.subarray(pos + 8, pos + 8 + length);
    const crc = png.readUInt32BE(pos + 8 + length);
    const check = png.subarray(pos + 4, pos + 8 + length);
    out.push({ tag, data: Buffer.from(data), crcOk: crc === crc32(check) });
    pos += 12 + length;
  }
  return out;
}

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

describe("crc32", () => {
  it("matches the reference value for a known input", () => {
    // zlib's CRC32 of "123456789" is a standard check value
    expect(crc32(Buffer.from("123456789"))).toBe(0xcbf43926);
  });

  it("is zero for empty input", () => {
    expect(crc32(Buffer.alloc(0))).toBe(0);
  });
});

describe("encodePng", () => {
  it("writes the signature and chunk order", () => {
    const png = encodePng(2, 2, Buffer.alloc(16));
    expect(png.subarray(0, 8)).toEqual(SIGNATURE);
    expect(chunksOf(png).map((c) => c.tag)).toEqual(["IHDR", "IDAT", "IEND"]);
  });

  it("writes IHDR fields", () => {
    const png = encodePng(3, 5, Buffer.alloc(3 * 5 * 4));
    const ihdr = chunksOf(png)[0]!.data;
    expect(ihdr.readUInt32BE(0)).toBe(3);
    expect(ihdr.readUInt32BE(4)).toBe(5);
    expect(ihdr[8]).toBe(8); // bit depth
    expect(ihdr[9]).toBe(6); // colour type RGBA
  });

  it("writes a valid CRC for every chunk", () => {
    const png = encodePng(4, 4, Buffer.alloc(64));
    for (const chunk of chunksOf(png)) {
      expect(chunk.crcOk, `CRC on ${chunk.tag}`).toBe(true);
    }
  });

  it("inflates IDAT to scanlines with filter bytes", () => {
    const width = 4;
    const height = 3;
    const rgba = Buffer.from(Array.from({ length: width * height * 4 }, (_, i) => i));
    const png = encodePng(width, height, rgba);
    const idat = Buffer.concat(chunksOf(png).filter((c) => c.tag === "IDAT").map((c) => c.data));
    const flat = inflateSync(idat);
    const stride = width * 4;
    expect(flat.length).toBe(height * (stride + 1));
    for (let y = 0; y < height; y++) {
      const at = y * (stride + 1);
      expect(flat[at]).toBe(0);
      expect(flat.subarray(at + 1, at + 1 + stride)).toEqual(rgba.subarray(y * stride, (y + 1) * stride));
    }
  });

  it("preserves a hard 1px edge, the case a JPEG codec smears", () => {
    const w = 64;
    const h = 8;
    const rgba = Buffer.alloc(w * h * 4);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const v = x >= 32 ? 255 : 0;
        const i = (y * w + x) * 4;
        rgba[i] = rgba[i + 1] = rgba[i + 2] = v;
        rgba[i + 3] = 255;
      }
    }
    const png = encodePng(w, h, rgba);
    const idat = Buffer.concat(chunksOf(png).filter((c) => c.tag === "IDAT").map((c) => c.data));
    const flat = inflateSync(idat);
    const row = flat.subarray(1, 1 + w * 4);
    expect(row[31 * 4]).toBe(0);
    expect(row[32 * 4]).toBe(255);
  });

  it("rejects a length mismatch", () => {
    expect(() => encodePng(4, 4, Buffer.alloc(10))).toThrow(/does not match/);
  });
});

describe("stripLinePrefixes", () => {
  it("removes a single line prefix", () => {
    expect(stripLinePrefixes("     1→payload")).toBe("payload");
  });

  it("removes several", () => {
    expect(stripLinePrefixes("     1→a\n     2→b")).toBe("a\nb");
  });

  it("leaves unprefixed text alone", () => {
    expect(stripLinePrefixes("plain")).toBe("plain");
  });
});

describe("parseHeader", () => {
  const good = ["1233", "754", "3718728", "4958304", "42", "ok", "Payload_1", "kcXb/5HF", "ff9daH3/"].join("\t");

  it("parses a good header", () => {
    const got = parseHeader(good);
    expect(got.width).toBe(1233);
    expect(got.rawBytes).toBe(3718728);
    expect(got.base64Chars).toBe(4958304);
    expect(got.module).toBe("Payload_1");
  });

  it("throws on a write failure", () => {
    const bad = ["1", "1", "4", "8", "1", "bad allocation", "P", "a", "b"].join("\t");
    expect(() => parseHeader(bad)).toThrow(/bad allocation/);
  });

  it("throws on a short header", () => {
    expect(() => parseHeader("only\ttwo\tfields")).toThrow();
  });
});

describe("base64 budget", () => {
  it("a 1233x754 capture fits the measured ceiling", () => {
    const chars = Math.ceil((1233 * 754 * 4 * 4) / 3);
    expect(chars).toBeLessThanOrEqual(MAX_BASE64_CHARS);
  });

  it("the ceiling is the measured value", () => {
    // 6,291,456 measured; 8 MB fails with "bad allocation"
    expect(MAX_BASE64_CHARS).toBe(6291456);
  });
});

// --------------------------------------------------------------------------- //
// save_path failure. The project's signature failure class: a failed write used
// to erase the record of a good capture, because the undeclared code it raised
// made `ToolError` throw *while building the error*. Mirrors
// `python/tests/test_capture.py::TestSavePathFailure`.
// --------------------------------------------------------------------------- //

const W = 2;
const H = 2;
const RGBA = Buffer.from(Array.from({ length: W * H * 4 }, (_, i) => i));

/**
 * The smallest Studio double that survives `captureRgba` end to end.
 *
 * Every field `parseHeader` reads is derived from the payload rather than typed
 * in, so a change to the encoding cannot leave the fake quietly wrong - a header
 * claiming the wrong base64 length would fault in the *integrity* checks and the
 * test would pass without ever reaching the write.
 *
 * Two `execute_luau` calls arrive: the capture and the scratch cleanup. They are
 * told apart by content, not by call order. `RBXCapture`, `FindFirstChild` and
 * `Destroy()` all appear in *both* snippets, so a guess on any of them returns ""
 * for the header - which faults in `parseHeader`, and the test then passes
 * without ever reaching the write it exists to pin.
 */
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

  return {
    call: async (name: string, args: Record<string, unknown> = {}) => {
      if (name === "script_read") {
        // script_read's own line prefix, which the stripper must remove.
        return CallToolResult.fromDict({
          content: [{ type: "text", text: `     1→${body}` }],
        });
      }
      if (name === "execute_luau") {
        const code = String(args["code"] ?? "");
        if (code.includes("CaptureScreenshot")) {
          return CallToolResult.fromDict({ content: [{ type: "text", text: header }] });
        }
        if (code.includes("if m then m:Destroy() end")) {
          return CallToolResult.fromDict({ content: [] });
        }
        throw new Error("unrecognised execute_luau call");
      }
      return CallToolResult.fromDict({ content: [] });
    },
  } as unknown as RobloxStudio;
}

function scratchDir(): string {
  return mkdtempSync(join(tmpdir(), "rbxcapture-test-"));
}

async function failsAt(savePath: string): Promise<ToolError> {
  try {
    await capturePng(capturingStudio(), { savePath });
  } catch (exc) {
    return exc as ToolError;
  }
  throw new Error("expected the write to fail");
}

describe("savePath failure", () => {
  it("the code is declared", async () => {
    // The regression itself. `INTERNAL_ERROR` is not in `ALL_CODES`, so
    // `ToolError`'s constructor threw *while constructing the error* and that
    // replaced it. Asserting only "it threw" would pass on the broken code too.
    const root = scratchDir();
    const err = await failsAt(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    expect(ALL_CODES.has(err.code), `undeclared code: ${err.code}`).toBe(true);
    expect(err.code).toBe(INVALID_ARGUMENT);
  });

  it("the capture record survives on the error", async () => {
    // The pixels were fine. A caller must be able to read that off the error.
    const root = scratchDir();
    const missing = join(root, "no-such-dir", "shot.png");
    const err = await failsAt(missing);
    rmSync(root, { recursive: true, force: true });
    expect(err.data["capture_succeeded"]).toBe(true);
    expect(err.data["width"]).toBe(W);
    expect(err.data["height"]).toBe(H);
    expect(err.data["raw_bytes"]).toBe(RGBA.length);
    expect(err.data["png_bytes"]).toBe(encodePng(W, H, RGBA).length);
    expect(err.data["mime"]).toBe("image/png");
    expect(err.data["lossless"]).toBe(true);
    expect(err.data["save_path"]).toBe(missing);
    // The timestamp, so a caller can tell this error from a later capture.
    expect(err.data["captured_at"]).toBeGreaterThan(0);
  });

  it("the message still says the capture succeeded", async () => {
    const root = scratchDir();
    const missing = join(root, "no-such-dir", "shot.png");
    const err = await failsAt(missing);
    rmSync(root, { recursive: true, force: true });
    expect(err.message).toContain("capture itself succeeded");
    expect(err.message).toContain(String(err.data["png_bytes"]));
    // `describe`, not the raw path: the message renders arguments as JSON so
    // both servers emit byte-identical strings, and `parity/errors.json` pins it.
    expect(err.message).toContain(describeValue(missing));
  });

  it("says the pixels are gone rather than retryable", async () => {
    // The scratch module is destroyed in a `finally` the moment the base64 is
    // read back, so the image is unrecoverable and the only route is a
    // re-capture. A message implying otherwise is a plausible wrong answer.
    const root = scratchDir();
    const err = await failsAt(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    expect(err.message).toContain("NOT retrievable");
    expect(err.message).toContain("re-capture");
  });

  it("states the truncation limit of the png_base64 alternative", async () => {
    // `png_base64` rides a return channel that truncates at exactly 100,015
    // characters with no error, and a real capture is ~4.96 M characters.
    // Recommending it without the limit walks the caller into a second silent
    // truncation.
    const root = scratchDir();
    const err = await failsAt(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    expect(err.message).toContain("100,015");
    expect(err.message).toContain("75,000");
  });

  it("the errno is a name, not a number", async () => {
    // So it is a branch key, and so both servers agree on the spelling.
    const root = scratchDir();
    const err = await failsAt(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    expect(err.data["errno"]).toBe("ENOENT");
  });

  it("the payload is JSON-serialisable", async () => {
    const root = scratchDir();
    const err = await failsAt(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    expect(() => JSON.stringify(err.toError())).not.toThrow();
  });

  it("the png_base64 route the message recommends actually works", async () => {
    // The advice the message gives, proven rather than asserted. If this stopped
    // working the recovery text would be a lie.
    const result = await capturePng(capturingStudio());
    expect(result.savePath).toBeUndefined();
    expect(Buffer.from(result.pngBase64, "base64")).toEqual(encodePng(W, H, RGBA));
  });
});

describe("a failed write leaves no damage", () => {
  it("a missing directory leaves nothing behind", async () => {
    const root = scratchDir();
    const err = await failsAt(join(root, "no-such-dir", "shot.png"));
    expect(err.code).toBe(INVALID_ARGUMENT);
    expect(existsSync(join(root, "no-such-dir"))).toBe(false);
    expect(readdirSync(root).filter((n) => n.startsWith(TEMP_PREFIX))).toEqual([]);
    rmSync(root, { recursive: true, force: true });
  });

  it("a destination that is a directory leaves no scratch inside it", async () => {
    const root = scratchDir();
    const target = join(root, "iam-a-directory");
    mkdirSync(target);
    const err = await failsAt(target);
    expect(err.code).toBe(INVALID_ARGUMENT);
    expect(readdirSync(target)).toEqual([]);
    expect(readdirSync(root).filter((n) => n.startsWith(TEMP_PREFIX))).toEqual([]);
    rmSync(root, { recursive: true, force: true });
  });

  it("a good write leaves no scratch either", async () => {
    const root = scratchDir();
    const target = join(root, "shot.png");
    const result = await capturePng(capturingStudio(), { savePath: target });
    expect(result.savePath).toBe(target);
    expect(readFileSync(target)).toEqual(encodePng(W, H, RGBA));
    expect(readdirSync(root).filter((n) => n.startsWith(TEMP_PREFIX))).toEqual([]);
    rmSync(root, { recursive: true, force: true });
  });

  it("a good write replaces an existing file", async () => {
    const root = scratchDir();
    const target = join(root, "shot.png");
    writeFileSync(target, "an older, longer capture that must be gone");
    await capturePng(capturingStudio(), { savePath: target });
    expect(readFileSync(target)).toEqual(encodePng(W, H, RGBA));
    rmSync(root, { recursive: true, force: true });
  });

  // The half-written-file case lives in `capture-partial-write.test.ts`. It needs
  // `vi.resetModules()` and a mocked `node:fs/promises`, and running it here
  // left a second copy of `errors.ts` alive for every test below - so their
  // `ToolError`s failed `instanceof` and classified as UNKNOWN.
});

// --------------------------------------------------------------------------- //
// The real JSON-RPC entry point, not `capturePng`. A unit test that calls the
// helper cannot see a missing `await` - that is how the relay guard shipped dead
// while its unit tests passed.
// --------------------------------------------------------------------------- //

/**
 * An `MCPClient` double, for the dispatch path.
 *
 * `callCapture` wraps whatever it is handed in a real `RobloxStudio`, so this
 * has to speak the client's contract (`listTools` / `callTool`) rather than the
 * Studio's - a Studio-shaped double here would fail on a missing method and
 * never reach the code under test.
 */
function capturingClient(): MCPClient {
  const studio = capturingStudio();
  return {
    protocolVersion: "x",
    capabilities: {},
    serverInfo: {},
    async listTools() {
      return [];
    },
    async callTool(name: string, args: Record<string, unknown> = {}) {
      return studio.call(name, args);
    },
    async request() {
      throw new Error("should not forward");
    },
    async close() {},
  } as unknown as MCPClient;
}

async function dispatchCapture(savePath: string): Promise<Record<string, unknown>> {
  const sent: Record<string, unknown>[] = [];
  await handleExtendedMessage(
    capturingClient(),
    {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "extended_capture", arguments: { save_path: savePath, studio_id: "fake" } },
    },
    (m) => sent.push(m),
  );
  expect(sent).toHaveLength(1);
  return sent[0]!;
}

describe("savePath failure through dispatch", () => {
  it("the wire error carries a declared code", async () => {
    const root = scratchDir();
    const response = await dispatchCapture(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    expect(response["error"]).toBeDefined();
    const error = response["error"] as Record<string, unknown>;
    expect(error["code"]).toBe(-32000);
    const data = error["data"] as Record<string, unknown>;
    expect(ALL_CODES.has(String(data["code"])), `undeclared code: ${String(data["code"])}`).toBe(true);
    expect(data["code"]).toBe(INVALID_ARGUMENT);
  });

  it("the wire error still says the capture succeeded", async () => {
    // The specific behaviour fixed, asserted on the wire. Before the fix this
    // arrived as `UNKNOWN: unknown error code "INTERNAL_ERROR"` - no code, no
    // metadata, and the capture record gone.
    const root = scratchDir();
    const response = await dispatchCapture(join(root, "no-such-dir", "shot.png"));
    rmSync(root, { recursive: true, force: true });
    const error = response["error"] as Record<string, unknown>;
    expect(String(error["message"])).toContain("capture itself succeeded");
    const data = error["data"] as Record<string, unknown>;
    expect(data["capture_succeeded"]).toBe(true);
    expect(data["png_bytes"]).toBe(encodePng(W, H, RGBA).length);
    expect(data["width"]).toBe(W);
    expect(data["errno"]).toBe("ENOENT");
  });

  it("a good savePath through dispatch returns the capture", async () => {
    const root = scratchDir();
    const target = join(root, "shot.png");
    const response = await dispatchCapture(target);
    const onDisk = readFileSync(target);
    rmSync(root, { recursive: true, force: true });
    expect(response["error"]).toBeUndefined();
    expect(onDisk).toEqual(encodePng(W, H, RGBA));
    const result = response["result"] as { content: Array<{ text: string }> };
    const payload = JSON.parse(result.content[0]!.text) as Record<string, unknown>;
    expect(payload["save_path"]).toBe(target);
    expect(payload["png_bytes"]).toBe(onDisk.length);
    expect(payload["studio_id"]).toBe("fake");
  });
});
