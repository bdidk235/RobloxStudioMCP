"""Tests for the lossless capture helpers.

The Studio-facing half needs a live instance; everything here is pure and
covers the parts that silently corrupt data if they are wrong: PNG framing,
base64 payload validation, and the ``script_read`` line-prefix stripper.

The ``save_path`` failure path is covered in full because it is the project's
signature failure class: it once raised an undeclared code, which made
``ToolError`` throw *while building the error* and destroyed the one message
saying the pixels were fine. The caller was told it failed, was given no code
to branch on, and held nothing.
"""

import base64
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "python", "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp import extended_server as es  # noqa: E402
from roblox_studio_mcp.extended import capture as cap  # noqa: E402
from roblox_studio_mcp.extended.capture import (  # noqa: E402
    MAX_BASE64_CHARS,
    TEMP_PREFIX,
    _parse_header,
    capture_png,
    encode_png,
    strip_line_prefixes,
)
from roblox_studio_mcp.extended.errors import ALL_CODES, ToolError  # noqa: E402
from roblox_studio_mcp.types import CallToolResult  # noqa: E402

#: 2x2, small enough that nothing here builds a multi-megabyte buffer.
W, H = 2, 2
RGBA = bytes(range(W * H * 4))


def capturing_studio():
    """The smallest Studio double that survives ``capture_rgba`` end to end.

    Every field ``_parse_header`` reads is derived from the payload rather than
    typed in, so a change to the encoding cannot leave the fake quietly wrong -
    a header claiming the wrong ``base64_chars`` would fault in the *integrity*
    checks and the test would pass without ever reaching the write.
    """
    body = base64.b64encode(RGBA).decode("ascii")
    header = "\t".join(
        [str(W), str(H), str(len(RGBA)), str(len(body)), "1", "ok", "Payload_1", body[:8], body[-8:]]
    )

    class Studio:
        def __init__(self):
            self.calls = []

        async def call(self, name, arguments=None):
            code = str((arguments or {}).get("code", ""))
            self.calls.append(name)
            if name == "execute_luau":
                if "CaptureScreenshot" in code:
                    return CallToolResult.from_dict(
                        {"content": [{"type": "text", "text": header}]}
                    )
                if 'if m then m:Destroy() end' in code:
                    return CallToolResult.from_dict({"content": []})
                # Neither script. Raising beats returning "", which would fault
                # in `_parse_header` and let the test pass for the wrong reason.
                raise AssertionError("unrecognised execute_luau call")
            if name == "script_read":
                # script_read's own line prefix, which the stripper must remove.
                return CallToolResult.from_dict(
                    {"content": [{"type": "text", "text": "     1→" + body}]}
                )
            return CallToolResult.from_dict({"content": []})

    return Studio()


class CapturingClient:
    """An ``MCPClient`` double, for the dispatch path.

    ``_call_capture`` wraps whatever it is handed in a real ``RobloxStudio``, so
    this has to speak the client's contract (``list_tools`` / ``call_tool``)
    rather than the Studio's - a Studio-shaped double here would fail on a
    missing method and never reach the code under test.
    """

    def __init__(self):
        self.tools = []
        self.calls = []

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        code = str((arguments or {}).get("code", ""))
        if name == "execute_luau":
            if "CaptureScreenshot" in code:
                body = base64.b64encode(RGBA).decode("ascii")
                header = "\t".join(
                    [
                        str(W),
                        str(H),
                        str(len(RGBA)),
                        str(len(body)),
                        "1",
                        "ok",
                        "Payload_1",
                        body[:8],
                        body[-8:],
                    ]
                )
                return CallToolResult.from_dict(
                    {"content": [{"type": "text", "text": header}]}
                )
            if 'if m then m:Destroy() end' in code:
                return CallToolResult.from_dict({"content": []})
            raise AssertionError("unrecognised execute_luau call")
        if name == "script_read":
            body = base64.b64encode(RGBA).decode("ascii")
            return CallToolResult.from_dict(
                {"content": [{"type": "text", "text": "     1→" + body}]}
            )
        return CallToolResult.from_dict({"content": []})


def chunks_of(png):
    pos, out = 8, []
    while pos < len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        tag = png[pos + 4 : pos + 8]
        data = png[pos + 8 : pos + 8 + length]
        crc = int.from_bytes(png[pos + 8 + length : pos + 12 + length], "big")
        out.append((tag, data, crc))
        pos += 12 + length
    return out


class TestEncodePng(unittest.TestCase):
    def test_signature_and_order(self):
        png = encode_png(2, 2, bytes(16))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual([c[0] for c in chunks_of(png)], [b"IHDR", b"IDAT", b"IEND"])

    def test_ihdr_fields(self):
        png = encode_png(3, 5, bytes(3 * 5 * 4))
        ihdr = chunks_of(png)[0][1]
        w, h, depth, color = struct.unpack(">IIBB", ihdr[:10])
        self.assertEqual((w, h), (3, 5))
        self.assertEqual(depth, 8)
        self.assertEqual(color, 6)  # truecolour with alpha

    def test_every_crc_is_valid(self):
        png = encode_png(4, 4, bytes(range(64)))
        for tag, data, crc in chunks_of(png):
            self.assertEqual(crc, zlib.crc32(tag + data) & 0xFFFFFFFF, f"CRC on {tag!r}")

    def test_idat_inflates_to_scanlines_with_filter_bytes(self):
        rgba = bytes(range(4 * 3 * 4))
        png = encode_png(4, 3, rgba)
        idat = b"".join(d for t, d, _ in chunks_of(png) if t == b"IDAT")
        flat = zlib.decompress(idat)
        stride = 4 * 4
        self.assertEqual(len(flat), 3 * (stride + 1))
        for y in range(3):
            off = y * (stride + 1)
            self.assertEqual(flat[off], 0, "filter byte must be 0")
            self.assertEqual(flat[off + 1 : off + 1 + stride], rgba[y * stride : (y + 1) * stride])

    def test_is_lossless_for_a_realistic_buffer(self):
        # a gradient with a hard 1px edge, the case a JPEG codec smears
        w, h = 64, 8
        rgba = bytearray()
        for y in range(h):
            for x in range(w):
                v = 255 if x >= 32 else 0
                rgba += bytes((v, v, v, 255))
        png = encode_png(w, h, bytes(rgba))
        idat = b"".join(d for t, d, _ in chunks_of(png) if t == b"IDAT")
        flat = zlib.decompress(idat)
        stride = w * 4
        row = flat[1 : 1 + stride]
        for x in (31, 32):
            self.assertEqual(row[x * 4], 0 if x < 32 else 255, f"edge wrong at x={x}")

    def test_rejects_a_length_mismatch(self):
        with self.assertRaises(ValueError) as ctx:
            encode_png(4, 4, bytes(10))
        self.assertIn("does not match", str(ctx.exception))

    def test_empty_is_valid(self):
        png = encode_png(0, 0, b"")
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")


class TestStripLinePrefixes(unittest.TestCase):
    def test_removes_numbered_prefixes(self):
        self.assertEqual(strip_line_prefixes("     1→abc\n     2→def"), "abc\ndef")

    def test_single_line_overhead_is_removed(self):
        self.assertEqual(strip_line_prefixes("     1→payload"), "payload")

    def test_leaves_unprefixed_text_alone(self):
        self.assertEqual(strip_line_prefixes("no prefixes here"), "no prefixes here")

    def test_does_not_eat_a_digit_inside_content(self):
        # only a line-leading "N->" is a prefix
        self.assertEqual(strip_line_prefixes("a1→b"), "a1→b")


class TestParseHeader(unittest.TestCase):
    def test_parses_a_good_header(self):
        head = "\t".join(["1233", "754", "3718728", "4958304", "42", "ok", "Payload_1", "kcXb/5HF", "ff9daH3/"])
        got = _parse_header(head)
        self.assertEqual(got["width"], 1233)
        self.assertEqual(got["raw_bytes"], 3718728)
        self.assertEqual(got["base64_chars"], 4958304)
        self.assertEqual(got["module"], "Payload_1")
        self.assertFalse(got["b64_head"].startswith("ERR"))

    def test_raises_on_a_write_failure(self):
        head = "\t".join(["1", "1", "4", "8", "1", "bad allocation", "Payload_2", "a", "b"])
        with self.assertRaises(RuntimeError) as ctx:
            _parse_header(head)
        self.assertIn("bad allocation", str(ctx.exception))

    def test_raises_on_a_short_header(self):
        with self.assertRaises(ValueError):
            _parse_header("only\ttwo\tfields")


class TestBase64Budget(unittest.TestCase):
    def test_a_1233x754_capture_fits_the_ceiling(self):
        raw = 1233 * 754 * 4
        b64 = base64.b64encode(bytes(raw)).decode()
        self.assertLessEqual(len(b64), MAX_BASE64_CHARS)
        self.assertEqual(len(b64), 4958304)

    def test_ceiling_is_the_measured_value(self):
        # 6,291,456 measured; 8 MB fails with "bad allocation"
        self.assertEqual(MAX_BASE64_CHARS, 6291456)


class _WriteFailure(unittest.IsolatedAsyncioTestCase):
    """A ``save_path`` that cannot be written, and the record of the capture."""

    async def asyncSetUp(self):
        self.root = tempfile.mkdtemp(suffix="-rbxcapture-test")
        self.addCleanup(_rmtree, self.root)
        # A parent that does not exist: the failure every caller hits first, and
        # the one that is identical on every platform.
        self.missing = os.path.join(self.root, "no-such-dir", "shot.png")

    async def _fails(self, path):
        with self.assertRaises(ToolError) as ctx:
            await capture_png(capturing_studio(), save_path=path)
        return ctx.exception


def _rmtree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class TestSavePathFailure(_WriteFailure):
    """The defect: a failed write used to erase the record of a good capture."""

    async def test_the_code_is_declared(self):
        """The regression itself.

        ``INTERNAL_ERROR`` is not in ``ALL_CODES``, so ``ToolError.__init__``
        raised ``AssertionError`` *while constructing the error* and that
        replaced it. Asserting only "it raised" would have passed on the broken
        code too, which raised just as loudly.
        """
        err = await self._fails(self.missing)
        self.assertIn(err.code, ALL_CODES, "undeclared code: %r" % err.code)
        self.assertEqual(err.code, "INVALID_ARGUMENT")

    async def test_the_capture_record_survives(self):
        """The pixels were fine. A caller must be able to read that off the error."""
        err = await self._fails(self.missing)
        self.assertTrue(err.data["capture_succeeded"])
        self.assertEqual(err.data["width"], W)
        self.assertEqual(err.data["height"], H)
        self.assertEqual(err.data["raw_bytes"], len(RGBA))
        self.assertEqual(err.data["png_bytes"], len(encode_png(W, H, RGBA)))
        self.assertEqual(err.data["mime"], "image/png")
        self.assertTrue(err.data["lossless"])
        self.assertGreater(err.data["captured_at"], 0)
        self.assertEqual(err.data["save_path"], self.missing)

    async def test_the_message_still_says_the_capture_succeeded(self):
        """The part the bug actually destroyed: the prose, not just the fields.

        The path is compared in its ``describe()`` form, not raw. The message
        renders arguments as JSON on purpose, because that is what keeps the
        emitted error string stable (``parity/errors.json`` pins the result)
        - a Windows path therefore appears with doubled backslashes. Asserting
        the raw path would push someone to "fix" the rendering and break the
        contract, which is the more expensive mistake.
        """
        from roblox_studio_mcp.extended.errors import describe

        err = await self._fails(self.missing)
        self.assertIn("capture itself succeeded", err.message)
        self.assertIn(str(err.data["png_bytes"]), err.message)
        self.assertIn(describe(self.missing), err.message)

    async def test_it_says_the_pixels_are_gone(self):
        """"Omit save_path and retry the write" is not the advice - both fail.

        The scratch module is destroyed in a ``finally`` the moment the base64 is
        read back, so the image is unrecoverable and the only route is to
        re-capture. A message implying otherwise is a plausible wrong answer.
        """
        err = await self._fails(self.missing)
        self.assertIn("NOT retrievable", err.message)
        self.assertIn("re-capture", err.message)

    async def test_it_states_the_truncation_limit_of_the_alternative(self):
        """``png_base64`` is not a general escape, and used to be sold as one.

        It rides a return channel that truncates at exactly 100,015 characters
        with no error, and a real capture is ~4.96 M characters. Recommending it
        without the limit sends the caller into a second silent truncation.
        """
        err = await self._fails(self.missing)
        self.assertIn("100,015", err.message)
        self.assertIn("75,000", err.message)

    async def test_the_errno_is_a_name_not_a_number(self):
        """So it is a branch key, with the spelling the contract pins."""
        err = await self._fails(self.missing)
        self.assertEqual(err.data["errno"], "ENOENT")
        self.assertIsInstance(err.data["errno"], str)

    async def test_the_payload_is_json_serialisable(self):
        """It crosses the wire as JSON; a float timestamp or bytes would not."""
        import json

        err = await self._fails(self.missing)
        self.assertEqual(json.loads(json.dumps(err.to_error()))["data"]["png_bytes"], err.data["png_bytes"])

    async def test_the_png_base64_route_actually_works(self):
        """The advice the message gives, proven rather than asserted.

        If this stopped working the recovery text would be a lie, so it is
        tested directly: same Studio, no ``save_path``, and the base64 decodes
        back to the same PNG.
        """
        result = await capture_png(capturing_studio())
        self.assertNotIn("save_path", result)
        self.assertEqual(
            base64.b64decode(result["png_base64"], validate=True),
            encode_png(W, H, RGBA),
        )


class TestNoCorruptFileLeftBehind(_WriteFailure):
    """A failed write must not damage what is already on disk.

    ``open(path, "wb")`` truncates before the first byte lands, so a write that
    dies partway leaves a short file that reads as a finished capture - and
    destroys the good capture that was already there.
    """

    async def test_a_missing_directory_leaves_nothing_behind(self):
        err = await self._fails(self.missing)
        self.assertEqual(err.code, "INVALID_ARGUMENT")
        self.assertFalse(os.path.exists(os.path.dirname(self.missing)))
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(TEMP_PREFIX)], [])

    async def test_a_destination_that_is_a_directory_leaves_no_scratch(self):
        target = os.path.join(self.root, "iam-a-directory")
        os.mkdir(target)
        err = await self._fails(target)
        self.assertEqual(err.code, "INVALID_ARGUMENT")
        self.assertTrue(os.path.isdir(target), "the directory itself must survive")
        self.assertEqual(os.listdir(target), [], "no temp file may be left inside it")

    async def test_a_write_that_dies_partway_keeps_the_previous_capture(self):
        """The half-written-file case, made deterministic.

        ``os.fdopen`` is patched rather than the file system, because the real
        thing - a full disk, a quota, a killed process - cannot be produced on
        demand. The double keeps the real contract (an fd in, a context manager
        with ``write`` out) so it cannot pass for the wrong reason.
        """
        good = os.path.join(self.root, "shot.png")
        previous = encode_png(3, 1, bytes(12))
        with open(good, "wb") as handle:
            handle.write(previous)

        real_fdopen = os.fdopen

        class HalfWriter:
            def __init__(self, handle):
                self._handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self._handle.close()
                return False

            def write(self, data):
                # Part of the bytes land, then the disk says no.
                self._handle.write(data[: len(data) // 2])
                raise OSError(28, "No space left on device")

        def fake_fdopen(fd, mode="r", *args, **kwargs):
            return HalfWriter(real_fdopen(fd, mode, *args, **kwargs))

        with mock.patch.object(cap.os, "fdopen", side_effect=fake_fdopen):
            err = await self._fails(good)

        self.assertEqual(err.code, "INVALID_ARGUMENT")
        self.assertEqual(err.data["errno"], "ENOSPC")
        with open(good, "rb") as handle:
            self.assertEqual(
                handle.read(), previous, "the previous capture was truncated or replaced"
            )
        self.assertEqual(
            [n for n in os.listdir(self.root) if n.startswith(TEMP_PREFIX)],
            [],
            "the scratch file was left behind",
        )

    async def test_a_good_write_leaves_no_scratch_either(self):
        target = os.path.join(self.root, "shot.png")
        result = await capture_png(capturing_studio(), save_path=target)
        self.assertEqual(result["save_path"], target)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), encode_png(W, H, RGBA))
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(TEMP_PREFIX)], [])

    async def test_a_good_write_replaces_an_existing_file_atomically(self):
        target = os.path.join(self.root, "shot.png")
        with open(target, "wb") as handle:
            handle.write(b"an older, longer capture that must be gone")
        await capture_png(capturing_studio(), save_path=target)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), encode_png(W, H, RGBA))


class TestSavePathThroughDispatch(unittest.TestCase):
    """The real JSON-RPC entry point, not ``capture_png``.

    A unit test that calls the helper cannot see a missing ``await`` - that is
    how the relay guard shipped dead while its unit tests passed. This drives
    ``_handle_message``, which is what a client calls, and reads the JSON it
    would put on stdout.
    """

    def _call(self, save_path):
        import asyncio
        from unittest import mock as _mock

        sent = []
        with _mock.patch.object(es, "_send", side_effect=lambda m: sent.append(m)):
            asyncio.new_event_loop().run_until_complete(
                es._handle_message(
                    CapturingClient(),
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "extended_capture",
                            "arguments": {"save_path": save_path, "studio_id": "fake"},
                        },
                    },
                )
            )
        self.assertEqual(len(sent), 1, "one response, not several")
        return sent[0]

    def test_the_wire_error_carries_a_declared_code(self):
        with tempfile.TemporaryDirectory() as root:
            response = self._call(os.path.join(root, "no-such-dir", "shot.png"))
        self.assertIn("error", response)
        error = response["error"]
        self.assertEqual(error["code"], -32000)
        self.assertIn(error["data"]["code"], ALL_CODES)
        self.assertEqual(error["data"]["code"], "INVALID_ARGUMENT")

    def test_the_wire_error_still_says_the_capture_succeeded(self):
        """The specific behaviour fixed, asserted on the wire.

        Before the fix this arrived as ``UNKNOWN: unknown error code
        'INTERNAL_ERROR'`` - no code, no metadata, and the capture record gone.
        """
        with tempfile.TemporaryDirectory() as root:
            response = self._call(os.path.join(root, "no-such-dir", "shot.png"))
        error = response["error"]
        self.assertIn("capture itself succeeded", error["message"])
        self.assertTrue(error["data"]["capture_succeeded"])
        self.assertEqual(error["data"]["png_bytes"], len(encode_png(W, H, RGBA)))
        self.assertEqual(error["data"]["width"], W)
        self.assertEqual(error["data"]["errno"], "ENOENT")

    def test_a_good_save_path_through_dispatch_returns_the_capture(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "shot.png")
            response = self._call(target)
            with open(target, "rb") as handle:
                on_disk = handle.read()
        self.assertIn("result", response, response.get("error"))
        self.assertEqual(on_disk, encode_png(W, H, RGBA))
        import json

        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["save_path"], target)
        self.assertEqual(payload["png_bytes"], len(on_disk))
        self.assertEqual(payload["studio_id"], "fake")


if __name__ == "__main__":
    unittest.main()
