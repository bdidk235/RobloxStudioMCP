/**
 * Lossless viewport capture.
 *
 * Why this exists
 * ---------------
 * The relayed `screen_capture` returns JPEG only and silently ignores a
 * `format` argument, so a caller cannot distinguish "applied" from "ignored".
 * This path is lossless: it reads the framebuffer as raw RGBA and encodes PNG
 * host-side, with nothing compressed in between.
 *
 * The transport, and why it is shaped this way
 * --------------------------------------------
 * `execute_luau` *does* return values, but the response is truncated at exactly
 * **100,015 characters** (measured: 50 K returns complete, 100 K arrives
 * truncated with the tail missing). A 1233x754 viewport needs 4,958,304 base64
 * characters, so the return channel can never carry a capture. Values are used
 * for the small header only.
 *
 * The pixels travel through a scratch `ModuleScript` instead:
 *
 * - `ReadPixelsBuffer` has **no** 1024x1024 cap - 2048x1024 (8,388,608 bytes,
 *   2 M px) reads back exactly. Do not port Chrrxs' tile-reassembly loop; a
 *   single call is correct and simpler.
 * - The write ceiling is **6,291,456 bytes**; 8 MB fails with `bad allocation`.
 *   A 1233x754 capture is 4,958,304, so one module holds it with ~1.2x
 *   headroom. Larger viewports are refused rather than silently truncated.
 * - `script_read` returns the payload intact at that size. It prefixes every
 *   line with `     1->`; with a single-line payload that is 7 characters.
 *
 * Two API details that are easy to get wrong and cost real time:
 *
 * - the method is `ReadPixelsBuffer` - `ReadPixels` is not a member of
 *   `EditableImage`;
 * - the write is `ScriptEditorService:UpdateSourceAsync(target, cb)` -
 *   `UpdateSourceAsync` is not a member of `ModuleScript`.
 *
 * Pixel data must never be printed. A ~5 MB `print` has been observed to
 * permanently wedge `get_console_output` and `extended_watch_output` for a
 * Studio instance. The scratch module is created under `PluginGuiService`,
 * which is studio-only: it never replicates, never publishes, and cannot reach
 * the place file or a team create.
 */

import { deflateSync } from "node:zlib";
import { dirname, join, resolve as resolvePath } from "node:path";
import { randomUUID } from "node:crypto";

import type { RobloxStudio } from "../roblox.js";
import { describe, INVALID_ARGUMENT, ToolError } from "./errors.js";
import type { CallToolResult } from "../types.js";

/**
 * Luau that captures, base64-encodes, and chunks the payload into a scratch
 * module, then returns only a small header. The body never comes back through
 * the return channel because that truncates at ~100 K.
 */
/**
 * The scratch module write ceiling, measured. 8 MB fails with "bad allocation".
 *
 * Declared BEFORE `CAPTURE_LUAU`, which interpolates it. It was below the
 * template literal until the base64/chunk fix, where using it inside the literal
 * made it a temporal-dead-zone reference at module init - a crash on import,
 * which is the loudest possible failure and still worth avoiding.
 */
export const MAX_BASE64_CHARS = 6_291_456;

/**
 * Per-append slice. Now the ceiling, so a viewport that fits is written in one
 * call: appends only ever cost time (each rewrites the whole module), and
 * splitting never raises {@link MAX_BASE64_CHARS}.
 */
export const DEFAULT_CHUNK = MAX_BASE64_CHARS;

export const CAPTURE_LUAU = `
local PG = game:GetService("PluginGuiService")
local CS = game:GetService("CaptureService")
local AssetService = game:GetService("AssetService")
local SES = game:GetService("ScriptEditorService")

local opts = {CHUNK}
local holder = PG:FindFirstChild("RBXCapture")
if not holder then
    holder = Instance.new("Folder")
    holder.Name = "RBXCapture"
    holder.Parent = PG
end

-- One module per capture attempt, so a failed run cannot poison the next.
-- The name must be dot-free: script_read addresses instances by dot-notation
-- path, so a dot in the name silently truncates the lookup.
local name = "Payload_" .. string.format("%013d", math.floor(os.clock() * 1e9) % 1e13)
local m = Instance.new("ModuleScript")
m.Name = name
m.Parent = holder
holder:SetAttribute("active", name)

local contentId
CS:CaptureScreenshot(function(id) contentId = id end)
local deadline = os.clock() + 10
while contentId == nil and os.clock() < deadline do task.wait(0.1) end
if contentId == nil then
    return "ERR\\tno contentId\\t\\t\\t\\t\\t\\t\\t\\t"
end

local img = AssetService:CreateEditableImageAsync(Content.fromUri(contentId))
local w, h = math.floor(img.Size.X), math.floor(img.Size.Y)
local raw = img:ReadPixelsBuffer(Vector2.new(0, 0), Vector2.new(w, h))
img:Destroy()

-- EncodingService:Base64Encode DOES accept a buffer. The comment that used to
-- claim otherwise was a misdiagnosis this repo carried for a session, and it
-- cost a 70x slowdown: the hand-rolled loop it justified took 527 ms on a
-- 3,718,728-byte frame where the service takes 7.5 ms. Byte-for-byte identical
-- at every sampled offset, so this is a pure swap. Mirrors python capture.py.
local ES = game:GetService("EncodingService")
local b64 = buffer.tostring(ES:Base64Encode(raw))
local n = buffer.len(raw)   -- raw byte count, still reported in the result

-- One append, not 31. DEFAULT_CHUNK was 120000 justified as "the size the
-- project already uses for chunked writes" - a cargo-cult from writer.py, which
-- does not use it either. Every append rewrites the whole module, so N appends
-- write ~P*N/2 bytes: measured 59.5 MB / 3.60 s at 120,000 versus 5.0 MB / 1.70 s
-- in one write. 2.1x. Splitting NEVER raises the 6,291,456 ceiling, so a
-- payload over it fails at any chunk size - the answer there is downscale.
local chunkSize = tonumber(opts.chunk) or MAX_BASE64_CHARS
local written, total, failed = 0, 0, ""
while total < #b64 do
    local piece = b64:sub(total + 1, total + chunkSize)
    local ok, err = pcall(function()
        SES:UpdateSourceAsync(m, function(old) return old .. piece end)
    end)
    if not ok then
        failed = tostring(err):sub(1, 120)
        break
    end
    written += 1
    total = total + #piece
end

return table.concat({
    tostring(w),
    tostring(h),
    tostring(n),
    tostring(#b64),
    tostring(written),
    failed ~= "" and failed or "ok",
    name,
    b64:sub(1, 8),
    b64:sub(-8),
}, "\\t")
`;

const SCRATCH_ROOT = "game.PluginGuiService.RBXCapture";

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/** CRC-32 as PNG requires it, over a Buffer. */
export function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]!) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(tag: string, data: Buffer): Buffer {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(tag, "latin1"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([head, body, crc]);
}

/**
 * Encode raw RGBA as PNG. Lossless; no third-party dependency.
 *
 * Colour type 6 (truecolour with alpha), bit depth 8, filter 0 on every
 * scanline. Each row is prefixed with its filter byte, which PNG requires.
 */
export function encodePng(width: number, height: number, rgba: Buffer): Buffer {
  const expected = width * height * 4;
  if (rgba.length !== expected) {
    throw new Error(`RGBA length ${rgba.length} does not match ${width}x${height}x4 = ${expected}`);
  }

  const stride = width * 4;
  const raw = Buffer.alloc(height * (stride + 1));
  for (let y = 0; y < height; y++) {
    const at = y * (stride + 1);
    raw[at] = 0; // filter type 0 (None)
    rgba.copy(raw, at + 1, y * stride, (y + 1) * stride);
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(raw, { level: 6 })),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

export interface CaptureHeader {
  width: number;
  height: number;
  rawBytes: number;
  base64Chars: number;
  chunks: number;
  module: string;
  b64Head: string;
  b64Tail: string;
}

export function parseHeader(text: string): CaptureHeader {
  const fields = text.trim().split("\t");
  if (fields.length < 9) {
    throw new Error(`unexpected capture header: ${describe(text.slice(0, 200))}`);
  }
  const status = fields[5]!;
  if (status !== "ok") throw new Error(`capture failed in Studio: ${status}`);
  return {
    width: Number(fields[0]),
    height: Number(fields[1]),
    rawBytes: Number(fields[2]),
    base64Chars: Number(fields[3]),
    chunks: Number(fields[4]),
    module: fields[6]!,
    b64Head: fields[7]!,
    b64Tail: fields[8]!,
  };
}

/**
 * Remove `script_read`'s `     1->` line-number prefixes.
 *
 * Reuses the writer's stripper rather than defining a second one: it is the
 * same prefix, it is already covered by tests, and the prefixes are never safe
 * to leave in - they corrupt the base64.
 */
import { stripLinePrefixes } from "./writer.js";
export { stripLinePrefixes };

async function discard(studio: RobloxStudio, module: string): Promise<void> {
  const code =
    'local h = game:GetService("PluginGuiService"):FindFirstChild("RBXCapture")\n' +
    `local m = h and h:FindFirstChild("${module}")\n` +
    "if m then m:Destroy() end";
  try {
    await studio.call("execute_luau", { code, datamodel_type: "Edit" });
  } catch {
    // a leftover studio-only instance is not worth failing the call
  }
}

export interface CaptureResult {
  width: number;
  height: number;
  rawBytes: number;
  pngBytes: number;
  mime: string;
  lossless: boolean;
  /** Unix seconds. Python's `captured_at`; it was absent here, so the two servers disagreed. */
  capturedAt?: number;
  savePath?: string;
  pngBase64?: string;
}

/**
 * Prefix for the sibling temporary file. Also the marker tests use to assert no
 * scratch survives a failed write.
 */
export const TEMP_PREFIX = ".rbxcapture-";

/**
 * What to do about a savePath that cannot be written. Appended to the message and
 * carried in `data` as `recovery`.
 *
 * Two claims here are load-bearing and both were false in the message this
 * replaces, which is why they are stated rather than implied:
 *
 * - **The pixels are gone.** They are *not* "still retrievable". The scratch
 *   module in Studio is destroyed in a `finally` the moment the base64 is read
 *   back, and the RGBA buffer dies with this call. Recovering the image means
 *   re-capturing.
 * - **`pngBase64` is not a general escape.** It rides the JSON-RPC result, which
 *   truncates at exactly 100,015 characters **with no error**. A 1233x754 viewport
 *   needs ~4,958,304 base64 characters, so "omit save_path" returns silently
 *   truncated garbage for any real capture. It is safe only while `pngBytes` stays
 *   under ~75,000, and the recommendation says so rather than sending an agent to a
 *   second silent truncation.
 *
 * Byte-identical to Python's `_CAPTURE_OK_RECOVERY`, including the snake_case
 * `save_path` / `png_bytes` because it names the caller's own arguments.
 */
export const CAPTURE_OK_RECOVERY =
  "The capture itself succeeded; the encoded PNG is discarded when this call " +
  "returns and is NOT retrievable afterwards, so re-capture rather than retry " +
  "the write. Either pass a save_path whose parent directory exists and is " +
  "writable, or omit save_path to get png_base64 in the result - the latter " +
  "has no filesystem dependency but rides this transport's return channel, " +
  "which truncates at exactly 100,015 characters with no error, so it is only " +
  "safe while png_bytes stays under about 75,000.";

/** `ENOENT`-style name for an fs error, matching what Python's `OSError` reports. */
function errnoName(exc: unknown): string {
  const code = (exc as NodeJS.ErrnoException | null)?.code;
  if (typeof code === "string" && code.length > 0) return code;
  if (typeof code === "number") return `E${code}`;
  const errno = (exc as NodeJS.ErrnoException | null)?.errno;
  return typeof errno === "number" && errno !== 0 ? `E${errno}` : "UNKNOWN";
}

/**
 * Write `data` to `path` through a sibling temp file and one rename.
 *
 * A bare `writeFile` truncates the destination *before* the first byte lands, so a
 * write that dies partway - full disk, quota, a killed process - leaves a short
 * file that reads as a finished capture and destroys whatever good capture was
 * already at that path. Both are worse than an error, and this tool exists because
 * the caller trusts the file.
 *
 * The temp file is created in the destination's own directory so the rename is
 * same-volume, and `fs.rename` swaps over an existing destination atomically on
 * both platforms. Any failure removes the temp file, so a failed write leaves the
 * directory exactly as it found it.
 */
export async function writeAtomic(path: string, data: Buffer): Promise<void> {
  const { open, rename, unlink } = await import("node:fs/promises");
  const directory = dirname(resolvePath(path));
  // `join`, not template concatenation: `path` is a Windows path here about half
  // the time and `/` as a separator is wrong for a directory component.
  const temp = join(directory, `${TEMP_PREFIX}${randomUUID()}.part`);
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(temp, "wx");
    await handle.writeFile(data);
    await handle.close();
    handle = undefined;
    await rename(temp, path);
  } catch (exc) {
    if (handle !== undefined) await handle.close().catch(() => undefined);
    await unlink(temp).catch(() => undefined);
    throw exc;
  }
}

/**
 * Capture the viewport and return raw RGBA.
 *
 * Raises rather than truncating: if the encoded payload would exceed
 * {@link MAX_BASE64_CHARS} the capture is refused up front, because a partial
 * image is worse than an error.
 */
export async function captureRgba(
  studio: RobloxStudio,
  options: { chunk?: number } = {},
): Promise<{ width: number; height: number; rgba: Buffer; header: CaptureHeader }> {
  const chunk = options.chunk ?? DEFAULT_CHUNK;
  const code = CAPTURE_LUAU.replace("{CHUNK}", `{ chunk = ${Math.trunc(chunk)} }`);
  const result: CallToolResult = await studio.call("execute_luau", {
    code,
    datamodel_type: "Edit",
  });
  const header = parseHeader(result.text());

  if (header.base64Chars > MAX_BASE64_CHARS) {
    await discard(studio, header.module);
    throw new Error(
      `${header.width}x${header.height} needs ${header.base64Chars} base64 chars, over the ` +
        `measured ${MAX_BASE64_CHARS} ceiling. Capture a smaller viewport, or tile the read instead.`,
    );
  }

  const path = `${SCRATCH_ROOT}.${header.module}`;
  let body: string;
  try {
    const read: CallToolResult = await studio.call("script_read", {
      target_file: path,
      should_read_entire_file: true,
    });
    body = stripLinePrefixes(read.text());
  } finally {
    await discard(studio, header.module);
  }

  if (body.length !== header.base64Chars) {
    throw new Error(`read back ${body.length} chars, expected ${header.base64Chars}`);
  }
  if (body.slice(0, 8) !== header.b64Head || body.slice(-8) !== header.b64Tail) {
    throw new Error("payload head/tail mismatch; the read is not intact");
  }

  const rgba = Buffer.from(body, "base64");
  if (rgba.length !== header.rawBytes) {
    throw new Error(`decoded ${rgba.length} bytes, expected ${header.rawBytes}`);
  }
  return { width: header.width, height: header.height, rgba, header };
}

/** Capture the viewport as a lossless PNG, writing it to `savePath` when given. */
export async function capturePng(
  studio: RobloxStudio,
  options: { savePath?: string; chunk?: number } = {},
): Promise<CaptureResult> {
  const { width, height, rgba } = await captureRgba(studio, options);
  const png = encodePng(width, height, rgba);
  const capturedAt = Date.now() / 1000;
  const result: CaptureResult = {
    width,
    height,
    rawBytes: rgba.length,
    pngBytes: png.length,
    mime: "image/png",
    lossless: true,
    capturedAt,
  };
  if (options.savePath) {
    // `INVALID_ARGUMENT`, not a code of our own inventing. The refusal is about
    // the value the caller supplied: a path that cannot be written is one they
    // have to change. It was `INTERNAL_ERROR`, which is not in `ALL_CODES`, so
    // `ToolError`'s constructor threw *while constructing the error* and the whole
    // thing was replaced by `Error: unknown error code "INTERNAL_ERROR"` -
    // destroying the one message that said the pixels were fine. The caller was
    // told it failed, was given no code to branch on, and was left with no pixels.
    //
    // `UNKNOWN` would be worse still: that is `classify`'s catch-all, and branching
    // on it catches every unrelated failure too.
    //
    // The one case the code slightly overstates is ENOSPC, where the path is fine
    // and the disk is full. `data.errno` distinguishes it, and the recovery line is
    // the same either way.
    try {
      await writeAtomic(options.savePath, png);
    } catch (exc) {
      const errno = errnoName(exc);
      throw new ToolError(
        INVALID_ARGUMENT,
        `captured a ${width}x${height} PNG (${png.length} bytes) but could not ` +
          `write it to ${describe(options.savePath)}: ${errno}. The parent ` +
          `directory must exist and be writable. ${CAPTURE_OK_RECOVERY}`,
        {
          capture_succeeded: true,
          width,
          height,
          raw_bytes: rgba.length,
          png_bytes: png.length,
          mime: "image/png",
          lossless: true,
          captured_at: capturedAt,
          save_path: options.savePath,
          errno,
          recovery: CAPTURE_OK_RECOVERY,
        },
      );
    }
    result.savePath = options.savePath;
  } else {
    result.pngBase64 = png.toString("base64");
  }
  return result;
}
