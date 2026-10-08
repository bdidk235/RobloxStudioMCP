"""The server must emit the contract error payload for the same request.

Reads ``contract/errors.json`` as the authority: every probe goes through the
**real code**, and each must produce the contract's code and message.

**Why this file exists.** Three measured defects, none of which the server's
own tests could have seen, because each only ever looked at its own output.

*Shape.* The tool-specific keys once nested under ``data.details`` instead of
spreading flat, and ``reject_unknown_arguments`` once attached no ``data`` at
all. A caller branching on ``data.unknown`` then read ``undefined`` - silently,
and on exactly the branch the caller wrote in order to *handle* the error.

*Value rendering.* ``repr`` writes ``None``/``True``/``'x'`` where the wire
format wants ``null``/``true``/``"x"``, and ``json.dumps``'s default
separators differ from the canonical compact form. Same request, two different
error strings; an agent that string-matches - which is what an agent does -
has to handle both. The server now renders through a shared ``describe``.

*Accept/reject.* A malformed edit was once cast to a mapping and ``undefined``
read out of it, so ``[42]`` was rejected at one layer and silently accepted
at another.

So the assertions are behavioural: every probe goes through the **real code**,
and each must produce the contract's code and message. Reading a
code off ``classify()`` for a hand-written string would test the classifier
rather than the raise site, which is exactly what the ``ToolError`` raise sites
exist to stop depending on.
"""

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp import extended_server as es  # noqa: E402
from roblox_studio_mcp.extended import breakpoints as bp  # noqa: E402
from roblox_studio_mcp.extended import capture as cap  # noqa: E402
from roblox_studio_mcp.extended import extensions as ext  # noqa: E402
from roblox_studio_mcp.extended import grep as gp  # noqa: E402
from roblox_studio_mcp.extended import updater as up  # noqa: E402
from roblox_studio_mcp.extended import writer as wr  # noqa: E402
from roblox_studio_mcp.extended.errors import (  # noqa: E402
    ALL_CODES,
    DATAMODAL_UNAVAILABLE,
    ToolError,
    _with_recovery,
    classify,
)

with open(os.path.join(_ROOT, "contract", "errors.json"), encoding="utf-8") as handle:
    CONTRACT = json.load(handle)


class FakeStudio:
    """Just enough of a Studio for the library layers to reach their own guards.

    Every probe below fails *before* it would call anything here, so a probe that
    started reaching for this object would be a probe that had stopped testing
    validation.
    """

    def __init__(self, files=None):
        self.files = files or {}

    async def script_read(self, target_path, **_kwargs):
        if target_path not in self.files:
            raise RuntimeError("could not find any instances")
        return _Text(self.files[target_path])

    async def call(self, *_args, **_kwargs):
        return _Text("")


class _Text:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _attempt(fn):
    """The code and message a caller would actually receive."""
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            result = _run(result)
    except ToolError as exc:
        return exc.code, exc.message
    except BaseException as exc:  # noqa: BLE001 - classified, like the relay loop
        err = classify(exc)
        return err.code, err.message
    raise AssertionError("expected a failure, got %r" % (result,))


# --- site probes ------------------------------------------------------------ #
# One thunk per entry in the contract's `sites`, so the contract drives the test
# rather than the other way round.

_HELPER = FakeStudio({"game.S.A": "hello", "game.S.Dup": "foo foo"})


def _empty_luau():
    handle = tempfile.NamedTemporaryFile("w", suffix=".luau", delete=False)
    handle.write("   \n")
    handle.close()
    return handle.name


_SITE_REAL_LUAU = None

SITES = {
    "breakpoints: line 0": lambda: bp.set_breakpoint(FakeStudio(), "BpTest", 0),
    "grep: query empty": lambda: gp.extended_script_grep(FakeStudio(), ""),
    "grep: max_results 0": lambda: gp.extended_script_grep(FakeStudio(), "x", max_results=0),
    "grep: max_results 'x'": lambda: gp.extended_script_grep(
        FakeStudio(), "x", max_results="x"
    ),
    "grep: context_lines 11": lambda: gp.extended_script_grep(
        FakeStudio(), "x", context_lines=11
    ),
    "grep: bad regex": lambda: gp.extended_script_grep(FakeStudio(), "([a", regex=True),
    "write: bad className": lambda: wr.write_script(
        FakeStudio({"game.S.A": "hi"}), "game.S.A", "x", className="Part"
    ),
    "write: non-game path": lambda: wr.write_script(FakeStudio(), "/tmp/x.lua", "hi"),
    "update: non-game path": lambda: up.update_script(
        FakeStudio(), "/tmp/x.lua", [("a", "b")]
    ),
    "update: no match": lambda: up.update_script(
        FakeStudio({"game.S.A": "hello"}), "game.S.A", [("nope", "x")], skip_missing=False
    ),
    "update: ambiguous": lambda: up.update_script(
        FakeStudio({"game.S.A": "foo foo"}), "game.S.A", [("foo", "bar")], skip_missing=False
    ),
    "update: noop": lambda: up.update_script(
        FakeStudio({"game.S.A": "hi"}), "game.S.A", [("hi", "hi")], skip_no_ops=False
    ),
    "update: empty old_string": lambda: up.update_script(
        FakeStudio({"game.S.A": "hi"}), "game.S.A", [("", "x")], skip_missing=False
    ),
    "update: bad edit shape": lambda: up.update_script(
        FakeStudio({"game.S.A": "hi"}), "game.S.A", [42]
    ),
    # `allow_outside=True` so this site tests the *missing file* fault rather
    # than tripping confinement first -- the path is on C:\, outside the root, so
    # without it the site would raise CAPABILITY_DENIED and assert the wrong
    # thing. Confinement has its own file, test_path_confinement.py.
    "insert: missing file": lambda: ext.insert_asset_from_file(
        FakeStudio(), r"C:\nope.luau", allow_outside=True
    ),
    "insert: bad parent": lambda: _insert_bad_parent(),
    "capture: rgba length mismatch": lambda: cap.encode_png(4, 4, bytes(10)),
    "capture: short header": lambda: cap._parse_header("only\ttwo\tfields"),
    "capture: studio bad allocation": lambda: cap._parse_header(
        "\t".join(["1", "1", "4", "8", "1", "bad allocation", "P", "a", "b"])
    ),
    "save_path: unwritable destination": lambda: _capture_bad_save_path(),
}


def _capture_bad_save_path():
    """A capture that succeeds and then cannot be written.

    Needs a Studio double that actually completes a capture - the fake above is
    built for probes that fail *before* they call anything, so reusing it here
    would make this probe pass for the wrong reason (it would fault on the
    capture, not on the write) and the declared code would never be reached.
    """
    root = tempfile.mkdtemp(suffix="-savepath")
    try:
        return cap.capture_png(
            _capturing_studio(), save_path=os.path.join(root, "no-such-dir", "shot.png")
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _capturing_studio():
    """The smallest Studio that survives `capture_rgba` end to end.

    Every field the header parser reads is derived from the payload rather than
    typed in, so a change to the encoding cannot leave the fake quietly wrong.
    """
    rgba = bytes(range(2 * 2 * 4))
    body = base64.b64encode(rgba).decode("ascii")
    header = "\t".join(
        ["2", "2", str(len(rgba)), str(len(body)), "1", "ok", "Payload_1", body[:8], body[-8:]]
    )

    class Studio:
        async def call(self, name, arguments=None):
            code = str((arguments or {}).get("code", ""))
            if name == "script_read":
                # script_read's own line prefix, which the stripper must remove.
                return _Text("     1→" + body)
            if name == "execute_luau":
                if "CaptureScreenshot" in code:
                    return _Text(header)
                if 'if m then m:Destroy() end' in code:
                    return _Text("")
                # Raising beats returning "": an empty header faults in
                # `_parse_header`, so the probe would pass without ever reaching
                # the write it is meant to be pinning.
                #
                # Discriminated on content, not on call order. `RBXCapture`,
                # `FindFirstChild` and `Destroy()` all appear in *both*
                # snippets, so a guess on any of them returns "" for the header.
                raise AssertionError("unrecognised execute_luau call")
            return _Text("")

    return Studio()


def _insert_bad_parent():
    """Needs a real file: a missing one is refused first, for a different reason."""
    path = _SITE_REAL_LUAU or tempfile.mktemp(suffix=".lua")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("print(1)")
    return ext.insert_asset_from_file(
           FakeStudio(), path, parent_path="/tmp", allow_outside=True
       )


def setUpModule():
    global _SITE_REAL_LUAU
    _SITE_REAL_LUAU = tempfile.mktemp(suffix=".lua")
    with open(_SITE_REAL_LUAU, "w", encoding="utf-8") as handle:
        handle.write("print(1)")


def tearDownModule():
    if _SITE_REAL_LUAU and os.path.exists(_SITE_REAL_LUAU):
        os.unlink(_SITE_REAL_LUAU)


# --- assertions ------------------------------------------------------------- #


class UnknownArgumentPayloads(unittest.TestCase):
    """The same ``data`` object, key for key, as the contract pins it."""

    def test_the_contract_is_not_empty(self):
        self.assertGreaterEqual(len(CONTRACT["unknown_arguments"]), 4)

    def test_the_data_payload_matches_the_contract(self):
        for case in CONTRACT["unknown_arguments"]:
            with self.subTest(tool=case["tool"], args=case["args"]):
                err = self._reject(case["tool"], case["args"])
                expected = dict(case["data"])
                wire_code = expected.pop("wire_data_code")
                payload = err.to_error()
                self.assertEqual(payload["code"], wire_code)
                self.assertEqual(payload["data"], expected)

    def _reject(self, tool, arguments):
        try:
            es._reject_unknown_arguments(tool, arguments)  # noqa: SLF001
        except ToolError as exc:
            return exc
        raise AssertionError("%s accepted %r" % (tool, arguments))

    def test_data_is_flat_not_nested(self):
        """The specific regression, pinned independently of the table.

        A fixture that merely happens to be flat today would not stop someone
        re-introducing the nesting while also updating the fixture.
        """
        data = self._reject("extended_capture", {"format": "png"}).to_error()["data"]
        self.assertNotIn("details", data)
        self.assertEqual(data["tool"], "extended_capture")
        self.assertEqual(data["unknown"], ["format"])
        self.assertEqual(data["accepted"], ["save_path", "studio_id"])

    def test_the_wire_payload_carries_the_code_inside_data(self):
        """What the relay loop actually sends.

        ``ToolError.to_error()`` keeps the code at the top level; the relay
        builder adds it to ``data`` too, so a caller reading either gets it.
        """
        data = es._error_payload(  # noqa: SLF001
            self._reject("extended_capture", {"format": "png"})
        )["data"]
        self.assertEqual(data["code"], "INVALID_ARGUMENT")
        self.assertNotIn("details", data)
        self.assertEqual(data["unknown"], ["format"])

    def test_the_data_payload_is_json_serialisable(self):
        """It crosses the wire as JSON; a tuple or a set here is a transport bug."""
        for case in CONTRACT["unknown_arguments"]:
            with self.subTest(tool=case["tool"]):
                json.dumps(self._reject(case["tool"], case["args"]).to_error())


class HandlerFaults(unittest.TestCase):
    """Driven through the real handlers, not through hand-written strings.

    ``classify`` is the wrong lens here: twenty-two of roughly twenty-seven
    caller faults were bare ``ValueError``/``RuntimeError`` raises that no pattern
    matched, and the fix was to raise ``ToolError`` at the raise site. Asserting
    here therefore tests the raise sites, which is where a regression appears.
    """

    def test_the_contract_is_not_empty(self):
        self.assertGreaterEqual(len(CONTRACT["handlers"]), 12)

    def test_each_fault_carries_its_declared_code(self):
        for case in CONTRACT["handlers"]:
            with self.subTest(case=case["name"]):
                code, _message = self._call(case)
                self.assertEqual(code, case["code"])

    def test_each_fault_says_the_declared_words(self):
        """Not a wording preference: divergent wording once shipped here.

        An agent branches on the code and *reads* the message. Wording that
        drifts from the contract means the agent has to handle both, and the
        spec claim is false.
        """
        for case in CONTRACT["handlers"]:
            with self.subTest(case=case["name"]):
                _code, message = self._call(case)
                self.assertEqual(message, case["message"])

    def test_its_data_matches_when_the_contract_pins_it(self):
        for case in CONTRACT["handlers"]:
            if "data" not in case:
                continue
            with self.subTest(case=case["name"]):
                err = self._tool_error(case)
                self.assertEqual(err.data, case["data"])

    def test_no_caller_fault_falls_through_to_unknown(self):
        """The bug this whole change exists to fix.

        Asserted as a property, so updating the table to hide a regression does
        not update this check at the same time.
        """
        for case in CONTRACT["handlers"]:
            with self.subTest(case=case["name"]):
                self.assertNotEqual(case["code"], "UNKNOWN")

    def _tool_error(self, case):
        try:
            _run(es._EXTENDED_HANDLERS[case["tool"]](None, case["args"]))  # noqa: SLF001
        except ToolError as exc:
            return exc
        except BaseException as exc:  # noqa: BLE001
            return classify(exc)
        raise AssertionError(case["name"])

    def _call(self, case):
        return self._tool_error(case).code, self._tool_error(case).message


class LibrarySiteFaults(unittest.TestCase):
    """The library layers, which are public entry points in their own right.

    These are reachable without going through a server handler, so validating
    only at the handler would leave a hole.
    """

    def test_the_contract_is_not_empty(self):
        self.assertGreaterEqual(len(CONTRACT["sites"]), 15)

    def test_every_site_in_the_contract_is_driven(self):
        """A contract entry nothing exercises is a comment that looks like a test."""
        for case in CONTRACT["sites"]:
            with self.subTest(site=case["site"]):
                self.assertIn(case["site"], SITES, "no probe for this contract entry")

    def test_every_probe_is_in_the_contract(self):
        for name in SITES:
            self.assertIn(
                name, [c["site"] for c in CONTRACT["sites"]], "probe not pinned"
            )

    def test_each_site_carries_its_declared_code(self):
        for case in CONTRACT["sites"]:
            with self.subTest(site=case["site"]):
                code, _message = _attempt(SITES[case["site"]])
                self.assertEqual(code, case["code"])

    def test_each_site_says_the_declared_words(self):
        for case in CONTRACT["sites"]:
            if case.get("code_only"):
                continue
            with self.subTest(site=case["site"]):
                _code, message = _attempt(SITES[case["site"]])
                self.assertEqual(message, case["message"])

    def test_a_conversion_decision_records_why(self):
        for case in CONTRACT["sites"]:
            if case["code"] == "INVALID_ARGUMENT" or case.get("why"):
                continue
            self.fail("%s is not INVALID_ARGUMENT and records no reason" % case["site"])

    def test_the_integrity_checks_stay_off_invalid_argument(self):
        """The capture sites check *our* consistency, not the caller's request.

        INVALID_ARGUMENT would send the caller off to edit a request that was
        fine, which is worse than an honest UNKNOWN.
        """
        for case in CONTRACT["sites"]:
            if not case["site"].startswith("capture:"):
                continue
            with self.subTest(site=case["site"]):
                self.assertNotEqual(case["code"], "INVALID_ARGUMENT")


class DeliberateNonArgumentFaults(unittest.TestCase):
    """Codes chosen *against* the grain, recorded so they are not "fixed".

    Each is a site that looks like a caller fault and is not, or is a caller
    fault a looser code would have swept up. Converting them mechanically would
    tell the caller to do something that cannot help.
    """

    def test_each_stays_on_its_declared_code(self):
        for case in CONTRACT["deliberately_not_argument_faults"]:
            with self.subTest(site=case["site"]):
                self.assertEqual(classify(RuntimeError(case["message"])).code, case["code"])

    def test_each_records_why(self):
        """A decision without a recorded reason gets reversed by the next reader."""
        for case in CONTRACT["deliberately_not_argument_faults"]:
            self.assertTrue(case.get("why"), case["site"])

    def test_a_text_edit_miss_is_not_not_found(self):
        """The one most likely to be "corrected" back to NOT_FOUND.

        ``NOT_FOUND``'s recovery is to re-list the DataModel, which cannot work
        for an edit whose target was just read successfully.
        """
        code, _message = _attempt(SITES["update: no match"])
        self.assertEqual(code, "INVALID_ARGUMENT")
        self.assertNotEqual(code, "NOT_FOUND")

    def test_a_host_file_miss_is_not_not_found(self):
        code, _message = _attempt(SITES["insert: missing file"])
        self.assertEqual(code, "INVALID_ARGUMENT")
        self.assertNotEqual(code, "NOT_FOUND")


class Vocabulary(unittest.TestCase):
    def test_every_declared_code_is_in_the_vocabulary(self):
        declared = (
            CONTRACT["handlers"]
            + CONTRACT["sites"]
            + CONTRACT["deliberately_not_argument_faults"]
        )
        for case in declared:
            label = case.get("name") or case["site"]
            self.assertIn(case["code"], ALL_CODES, label)


class RecoveryWording(unittest.TestCase):
    """The DATAMODAL_UNAVAILABLE hint, pinned against its measured failure.

    Found by hostile fuzzing: breakpoints in Edit mode with NO session running
    got "Server datamodel is not available in Edit mode" plus a hint written
    for the opposite case - asserting a session is running and recommending
    action='stop', which terminates the process rather than ending a session.
    An agent following it destroys the Studio it was trying to read.
    """

    def _recovered(self):
        return _with_recovery(
            ToolError(
                DATAMODAL_UNAVAILABLE,
                "Server datamodel is not available in Edit mode",
            )
        ).message

    def test_does_not_assert_a_session_is_running(self):
        self.assertNotIn("while a play session is running", self._recovered())

    def test_covers_both_directions(self):
        text = self._recovered()
        self.assertIn("Edit mode", text)
        self.assertIn("play session", text)

    def test_never_presents_stop_as_session_control(self):
        text = self._recovered()
        self.assertNotIn("stop it first", text)
        self.assertIn("terminates the Studio", text)

    def test_the_engine_message_stays_first(self):
        text = self._recovered()
        self.assertTrue(
            text.startswith("Server datamodel is not available in Edit mode"),
            text[:80],
        )


if __name__ == "__main__":
    unittest.main()
