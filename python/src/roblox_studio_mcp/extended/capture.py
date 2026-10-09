"""Lossless viewport capture.

Why this exists
---------------
The relayed ``screen_capture`` returns JPEG only and silently ignores a
``format`` argument, so a caller cannot distinguish "applied" from "ignored".
This path is lossless: it reads the framebuffer as raw RGBA and encodes PNG
host-side, with nothing compressed in between.

The transport, and why it is shaped this way
--------------------------------------------
``execute_luau`` *does* return values, but the response is truncated at exactly
**100,015 characters** (measured: 50 K returns complete, 100 K arrives
truncated with the tail missing). A 1233x754 viewport needs 4,958,304 base64
characters, so the return channel can never carry a capture. Values are used
for the small header only.

The pixels travel through a scratch ``ModuleScript`` instead:

* ``ReadPixelsBuffer`` has **no** 1024x1024 cap - 2048x1024 (8,388,608 bytes,
  2 M px) reads back exactly. Do not port Chrrxs' tile-reassembly loop; a
  single call is correct and simpler.
* The write ceiling is **6,291,456 bytes**; 8 MB fails with ``bad allocation``.
  A 1233x754 capture is 4,958,304, so one module holds it with ~1.2x headroom.
  Larger viewports need ``downscale`` or must spread across modules.
* ``script_read`` returns the payload intact at that size. It prefixes every
  line with ``     1->``; with a single-line payload that is 7 characters.

Two API details that are easy to get wrong and cost real time:

* the method is ``ReadPixelsBuffer`` - ``ReadPixels`` is not a member of
  ``EditableImage``;
* the write is ``ScriptEditorService:UpdateSourceAsync(target, cb)`` -
  ``UpdateSourceAsync`` is not a member of ``ModuleScript``.

A third, corrected: ``EncodingService:Base64Encode`` **does** accept a buffer and
is the fast path. This file previously carried a hand-rolled per-3-byte base64
loop on the stated grounds that the service rejected buffers. It does not. The
loop cost 527 ms on a 3,718,728-byte frame where the service takes 7.5 ms, for
byte-identical output - a 70x tax paid for a misdiagnosis. Recorded here because
the same claim is likely to be repeated from memory, and it is false.

``EncodingService:CompressBuffer`` also works, with a required second argument of
type ``CompressionAlgorithm`` where only the value ``0`` is accepted. It is not
used: the 4,958,304-character payload already fits under the write ceiling, a
real rendered frame compresses far less than a synthetic gradient does, and
Python 3.12 has no stdlib zstd, so the saving would land as a host-side
dependency.

Pixel data must never be printed. A ~5 MB ``print`` has been observed to
permanently wedge ``get_console_output`` and ``extended_watch_output`` for a
Studio instance. The scratch module is created under ``PluginGuiService``, which
is studio-only: it never replicates, never publishes, and cannot reach the place
file or a team create.
"""

from __future__ import annotations

import base64
import contextlib
import errno as _errno
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib
from typing import Any, Dict, Optional, Tuple

from ..roblox import RobloxStudio
from .errors import CAPABILITY_DENIED, describe, ToolError
from ..types import CallToolResult
from .extensions import _confined

#: Luau that captures, base64-encodes, and chunks the payload into a scratch
#: module, then returns only a small header. The body never comes back through
#: the return channel because that truncates at ~100 K.
_CAPTURE_LUAU = """
local PG = game:GetService("PluginGuiService")
local CS = game:GetService("CaptureService")
local AssetService = game:GetService("AssetService")
local SES = game:GetService("ScriptEditorService")
local ES = game:GetService("EncodingService")

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
    return "ERR\tno contentId\t\t\t\t"
end

local img = AssetService:CreateEditableImageAsync(Content.fromUri(contentId))
local w, h = math.floor(img.Size.X), math.floor(img.Size.Y)
local raw = img:ReadPixelsBuffer(Vector2.new(0, 0), Vector2.new(w, h))
img:Destroy()
-- Read once, here: the header needs the byte count, and reading it again from the
-- encoded string would be a second source of truth.
local n = buffer.len(raw)

-- EncodingService:Base64Encode is buffer-native and 70x faster than doing this by
-- hand. Measured on this machine over a real 1233x754x4 buffer (3,718,728 B):
-- Base64Encode 7.5 ms, the hand-rolled per-3-byte loop 527 ms, and the output is
-- byte-for-byte identical at every sampled offset.
--
-- An earlier note in this file claimed Base64Encode "rejects a buffer". That was
-- wrong, and the hand-rolled loop it justified was 70x the cost for nothing.
-- `buffer.tostring` is the other half of the win: it is a single C-level copy,
-- 5 ms for 4.9 MB, and it is safe here only because base64 output is pure ASCII,
-- so no byte reaches 0x80 and none is sign-extended.
local b64 = buffer.tostring(ES:Base64Encode(raw))

local chunkSize = tonumber(opts.chunk) or 120000
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
    w and tostring(w) or "0",
    tostring(h),
    tostring(n),
    tostring(#b64),
    tostring(written),
    failed ~= "" and failed or "ok",
    name,
    b64:sub(1, 8),
    b64:sub(-8),
}, "\\t")
"""

#: The scratch module write ceiling, measured. 8 MB fails with "bad allocation".
MAX_BASE64_CHARS = 6_291_456

#: Per-append slice.
#:
#: This is a **speed knob, not a capacity knob**, and confusing the two is what
#: made it 120,000 for so long: every append rewrites the whole module, so N
#: appends of a payload P write about P*N/2 bytes. Splitting one module never
#: raises the ceiling - a payload over it fails at any chunk size - so the only
#: reason to append more than once is to stay under the ceiling, which one
#: append of ``MAX_BASE64_CHARS`` already does.
#:
#: Measured end to end on a 1233x754 viewport, all sizes returning byte-exact
#: RGBA:
#:
#: | chunk | appends | approx bytes written | elapsed |
#: |---|---|---|---|
#: | 120,000 | 31 | 59.5 MB | 3.60 s |
#: | 1,000,000 | 4 | 10.0 MB | 1.85 s |
#: | 2,000,000 | 2 | 6.0 MB | 1.71 s |
#: | one append (4,958,304) | 1 | 5.0 MB | 1.70 s |
#:
#: Set to the ceiling so a payload that fits is written in one call. A single
#: 4,958,304-character write was measured working; 6,300,000 also worked, so the
#: documented ceiling is a module-size limit rather than a per-call one.
DEFAULT_CHUNK = MAX_BASE64_CHARS

_SCRATCH_ROOT = "game.PluginGuiService.RBXCapture"


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode raw RGBA as PNG. Lossless; no third-party dependency.

    Colour type 6 (truecolour with alpha), bit depth 8, filter 0 on every
    scanline. Each row is prefixed with its filter byte, which PNG requires.
    """
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(
            f"RGBA length {len(rgba)} does not match {width}x{height}x4 = {expected}"
        )

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += rgba[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _parse_header(text: str) -> Dict[str, Any]:
    fields = text.strip().split("\t")
    if len(fields) < 9:
        raise ValueError(f"unexpected capture header: {describe(text[:200])}")
    width, height, raw_len, b64_len, chunks, status, name, head, tail = fields[:9]
    if status != "ok":
        raise RuntimeError(f"capture failed in Studio: {status}")
    return {
        "width": int(width),
        "height": int(height),
        "raw_bytes": int(raw_len),
        "base64_chars": int(b64_len),
        "chunks": int(chunks),
        "module": name,
        "b64_head": head,
        "b64_tail": tail,
    }


_LINE_PREFIX = None


def strip_line_prefixes(text: str) -> str:
    """Remove ``script_read``'s ``     1->`` line-number prefixes.

    With a single-line payload this is a 7-character overhead, so it is cheap,
    but the prefixes are never safe to leave in: they corrupt the base64.
    """
    global _LINE_PREFIX
    if _LINE_PREFIX is None:
        import re

        _LINE_PREFIX = re.compile(r"^\s*\d+→", re.MULTILINE)
    return _LINE_PREFIX.sub("", text)


async def capture_rgba(
    studio: RobloxStudio, *, chunk: int = DEFAULT_CHUNK
) -> Tuple[int, int, bytes, Dict[str, Any]]:
    """Capture the viewport and return ``(width, height, raw_rgba, info)``.

    Raises rather than truncating: if the encoded payload would exceed
    :data:`MAX_BASE64_CHARS` the write is refused up front, because a partial
    capture is worse than an error.
    """
    code = _CAPTURE_LUAU.replace("{CHUNK}", f"{{ chunk = {int(chunk)} }}")
    result: CallToolResult = await studio.call(
        "execute_luau", {"code": code, "datamodel_type": "Edit"}
    )
    header = _parse_header(result.text())
    if header["base64_chars"] > MAX_BASE64_CHARS:
        await _discard(studio, header["module"])
        raise RuntimeError(
            f"{header['width']}x{header['height']} needs {header['base64_chars']} "
            f"base64 chars, over the measured {MAX_BASE64_CHARS} ceiling. "
            f"Capture a smaller viewport, or tile the read instead."
        )

    path = f"{_SCRATCH_ROOT}.{header['module']}"
    try:
        read: CallToolResult = await studio.call(
            "script_read",
            {"target_file": path, "should_read_entire_file": True},
        )
        body = strip_line_prefixes(read.text())
    finally:
        await _discard(studio, header["module"])

    if len(body) != header["base64_chars"]:
        raise RuntimeError(
            f"read back {len(body)} chars, expected {header['base64_chars']}"
        )
    if body[:8] != header["b64_head"] or body[-8:] != header["b64_tail"]:
        raise RuntimeError("payload head/tail mismatch; the read is not intact")

    rgba = base64.b64decode(body, validate=True)
    if len(rgba) != header["raw_bytes"]:
        raise RuntimeError(
            f"decoded {len(rgba)} bytes, expected {header['raw_bytes']}"
        )
    return header["width"], header["height"], rgba, header


async def _discard(studio: RobloxStudio, module: str) -> None:
    """Remove the scratch module, whatever happened. It is studio-only."""
    code = (
        'local h = game:GetService("PluginGuiService"):FindFirstChild("RBXCapture")\n'
        f'local m = h and h:FindFirstChild("{module}")\n'
        "if m then m:Destroy() end"
    )
    try:
        await studio.call("execute_luau", {"code": code, "datamodel_type": "Edit"})
    except Exception:
        pass  # a leftover studio-only instance is not worth failing the call


#: Prefix for the sibling temporary file. Also the marker tests use to assert no
#: scratch survives a failed write.
TEMP_PREFIX = ".rbxcapture-"


def _write_atomic(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` through a sibling temp file and one rename.

    A bare ``open(path, "wb")`` truncates the destination *before* the first
    byte lands, so a write that dies partway - full disk, quota, a killed
    process - leaves a short file that reads as a finished capture and destroys
    whatever good capture was already at that path. Both are worse than an
    error, and this tool exists because the caller trusts the file.

    The temp file is created in the destination's own directory so the rename
    is same-volume, and on Windows ``os.replace`` swaps over an existing
    destination atomically. Any failure removes the temp file, so a failed
    write leaves the directory exactly as it found it.
    """
    directory = os.path.dirname(os.path.abspath(path)) or os.curdir
    handle_fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".part", dir=directory)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def _errno_name(exc: OSError) -> str:
    """``ENOENT``-style name for an OS error.

    The symbolic name rather than the number or ``strerror``: the number is
    platform-specific and ``strerror`` is per-OS prose. A stable name is what
    makes ``data.errno`` a branch key instead of something to string-match.
    """
    if exc.errno is not None:
        return _errno.errorcode.get(exc.errno, "E%d" % exc.errno)
    return type(exc).__name__


#: What to do about a save_path that cannot be written. Appended to the message
#: and carried in ``data`` as ``recovery``.
#:
#: Two claims here are load-bearing and both were false in the message this
#: replaces, which is why they are stated rather than implied:
#:
#: * **The pixels are gone.** They are *not* "still retrievable". The scratch
#:   module in Studio is destroyed in a ``finally`` the moment the base64 is
#:   read back, and the RGBA buffer dies with this call. Recovering the image
#:   means re-capturing.
#: * **``png_base64`` is not a general escape.** It rides the JSON-RPC result,
#:   which truncates at exactly 100,015 characters **with no error**. A 1233x754
#:   viewport needs ~4,958,304 base64 characters, so "omit save_path" returns
#:   silently truncated garbage for any real capture. It is safe only while
#:   ``png_bytes`` stays under ~75,000, and the recommendation says so rather
#:   than sending an agent to a second silent truncation.
_CAPTURE_OK_RECOVERY = (
    "The capture itself succeeded; the encoded PNG is discarded when this call "
    "returns and is NOT retrievable afterwards, so re-capture rather than "
    "retry the write. Either pass a save_path whose parent directory exists and "
    "is writable, or omit save_path to get png_base64 in the result - the latter "
    "has no filesystem dependency but rides this transport's return channel, "
    "which truncates at exactly 100,015 characters with no error, so it is only "
    "safe while png_bytes stays under about 75,000."
)


async def capture_png(
    studio: RobloxStudio,
    *,
    save_path: Optional[str] = None,
    chunk: int = DEFAULT_CHUNK,
) -> Dict[str, Any]:
    """Capture the viewport as a lossless PNG.

    Writes to ``save_path`` when given, and always returns the metadata plus
    the encoded bytes' length so a caller can decide what to do next.

    A write that fails raises :class:`ToolError` with ``INVALID_ARGUMENT`` and
    the capture's own metadata attached, so a caller is never left with "it
    failed" and nothing to show for it. See :data:`_CAPTURE_OK_RECOVERY`.
    """
    width, height, rgba, info = await capture_rgba(studio, chunk=chunk)
    png = encode_png(width, height, rgba)

    captured_at = time.time()
    result: Dict[str, Any] = {
        "width": width,
        "height": height,
        "raw_bytes": len(rgba),
        "png_bytes": len(png),
        "mime": "image/png",
        "lossless": True,
        "captured_at": captured_at,
    }
    if save_path:
        # Confined before anything touches the disk: without this, `save_path`
        # truncates any user-writable file with PNG bytes while the tool
        # advertises `readOnlyHint`. A capture outside the working directory
        # is refused with CAPABILITY_DENIED, naming the recovery.
        #
        # An existing directory skips confinement and fails at the write with
        # INVALID_ARGUMENT as before: `_confined` refuses non-files with a
        # read-tool message ("these tools read files"), which would lie about
        # a write destination, and a directory is not writable either way.
        try:
            destination_is_dir = Path(save_path).expanduser().resolve().is_dir()
        except (OSError, RuntimeError):
            destination_is_dir = False
        if not destination_is_dir:
            try:
                save_path = str(_confined(save_path))
            except ToolError as exc:
                # `_confined` speaks for read tools ("read it", "allow_outside",
                # a parameter this tool does not take). Same code, recovery
                # rewritten for a write: the actionable half is the path.
                raise ToolError(
                    CAPABILITY_DENIED,
                    f"save_path is outside the working directory: "
                    f"{describe(save_path)}. Pass a save_path inside the "
                    f"working directory, or omit save_path to get png_base64 "
                    f"in the result.",
                    path=str(exc.data.get("path", save_path)),
                    root=str(exc.data.get("root", "")),
                ) from None
        # `INVALID_ARGUMENT`, not a code of our own inventing. The refusal is
        # about the value the caller supplied: a path that cannot be written is
        # one they have to change. It was `INTERNAL_ERROR`, which is not in
        # `ALL_CODES`, so `ToolError.__init__` raised *while constructing the
        # error* and the whole thing was replaced by
        # `AssertionError: unknown error code 'INTERNAL_ERROR'` - destroying the
        # one message that said the pixels were fine. The caller was told it
        # failed, was given no code to branch on, and was left with no pixels.
        #
        # `UNKNOWN` would be worse still: that is `classify`'s catch-all, and
        # branching on it catches every unrelated failure too.
        #
        # The one case the code slightly overstates is ENOSPC, where the path is
        # fine and the disk is full. `data.errno` distinguishes it, and the
        # recovery line is the same either way.
        try:
            _write_atomic(save_path, png)
        except OSError as exc:
            raise ToolError(
                "INVALID_ARGUMENT",
                f"captured a {width}x{height} PNG ({len(png)} bytes) but could "
                f"not write it to {describe(save_path)}: {_errno_name(exc)}. "
                f"The parent directory must exist and be writable. "
                f"{_CAPTURE_OK_RECOVERY}",
                capture_succeeded=True,
                width=width,
                height=height,
                raw_bytes=len(rgba),
                png_bytes=len(png),
                mime="image/png",
                lossless=True,
                captured_at=captured_at,
                save_path=save_path,
                errno=_errno_name(exc),
                recovery=_CAPTURE_OK_RECOVERY,
            ) from None
        result["save_path"] = save_path
    else:
        result["png_base64"] = base64.b64encode(png).decode("ascii")
    return result
