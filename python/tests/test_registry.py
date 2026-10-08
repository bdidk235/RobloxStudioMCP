"""Tests for the host-side Studio instance registry."""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from roblox_studio_mcp.extended import registry
from roblox_studio_mcp.extended.registry import EXPIRE_AFTER_SECONDS
from roblox_studio_mcp.errors import MCPToolError
from roblox_studio_mcp.types import CallToolResult, Tool


def _console_with(*lines: str) -> CallToolResult:
    return CallToolResult.from_dict(
        {"content": [{"type": "text", "text": "\n".join(lines)}]}
    )


class FakeStudio:
    """Minimal RobloxStudio stand-in: answers the two calls the reader makes."""

    def __init__(self, console_lines, fail_execute: bool = False):
        self._console = console_lines
        self._fail_execute = fail_execute
        self.executed: list[dict] = []
        self.studio_id = "sid-1"

    async def call(self, name, arguments=None):
        arguments = arguments or {}
        if name == "execute_luau":
            if self._fail_execute:
                raise MCPToolError("execute_luau is unavailable here")
            self.executed.append(arguments)
            return CallToolResult.from_dict({"content": [{"type": "text", "text": "nil"}]})
        if name == "get_console_output":
            return _console_with(*self._console)
        raise AssertionError(f"unexpected tool {name!r}")


class FakeListClient:
    def __init__(self, studios):
        self._studios = studios

    async def list_studios(self):
        return list(self._studios)

    @property
    def client(self):
        return self


class RegistryTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.mkdtemp()
        self.path = os.path.join(directory, "studios.json")

    def read(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)


class TestInBandIdentity(RegistryTestCase):
    async def test_parses_a_tagged_line(self):
        studio = FakeStudio(['MCPID\t0_185967\tPlace1\t123\t456'])
        identity = await registry.read_in_band_identity(studio)
        self.assertEqual(
            identity,
            {"debug_id": "0_185967", "name": "Place1", "place_id": 123, "game_id": 456},
        )

    async def test_reads_in_edit_mode(self):
        studio = FakeStudio(["MCPID\t0_1\tX\t0\t0"])
        await registry.read_in_band_identity(studio)
        # A play session reports a different debug id, so this must be Edit.
        self.assertEqual(studio.executed[0]["datamodel_type"], "Edit")

    async def test_tolerates_surrounding_quoting_and_noise(self):
        # get_console_output can leave the tool's own quoting in place, and a
        # tab inside the payload arrives literally rather than escaped.
        studio = FakeStudio(
            [
                "some unrelated output",
                '"MCPID\t0_9\tNamed\t7\t8"',
                "trailing noise",
            ]
        )
        identity = await registry.read_in_band_identity(studio)
        self.assertIsNotNone(identity)
        self.assertEqual(identity["debug_id"], "0_9")
        self.assertEqual(identity["name"], "Named")

    async def test_returns_none_when_absent(self):
        studio = FakeStudio(["nothing tagged here"])
        self.assertIsNone(await registry.read_in_band_identity(studio))

    async def test_ignores_a_truncated_line(self):
        studio = FakeStudio(["MCPID\t0_1\tOnlyTwo"])
        self.assertIsNone(await registry.read_in_band_identity(studio))


class ChannelOrder(RegistryTestCase):
    """`return` is primary, `print` is the fallback. Both are pinned.

    The order is the whole point, so these assert on the *sequence* of tool
    calls rather than just the result: a test that only checked the identity
    would pass either way, and would not notice the console write coming back.
    """

    async def test_return_is_used_first_and_writes_nothing(self):
        studio = FakeStudio([])
        seen: list[str] = []

        async def call(name, arguments=None):
            seen.append(name)
            if name == "execute_luau":
                studio.executed.append(arguments or {})
                return _console_with('MCPID\t0_7\tPlace1\t0\t0')
            return _console_with()

        studio.call = call
        identity = await registry.read_in_band_identity(studio)
        self.assertEqual(identity["debug_id"], "0_7")
        # One call, no console read, and the script must not print.
        self.assertEqual(seen, ["execute_luau"])
        code = studio.executed[0]["code"]
        self.assertIn("return", code)
        self.assertNotIn("print", code)

    async def test_falls_back_to_print_when_the_return_is_empty(self):
        # The return channel coming back blank is what the fallback exists for,
        # and it must still resolve rather than report no identity.
        studio = FakeStudio(["MCPID\t0_9\tNamed\t7\t8"])
        seen: list[str] = []

        async def call(name, arguments=None):
            seen.append(name)
            if name == "execute_luau":
                studio.executed.append(arguments or {})
                return _console_with("")  # the return yielded nothing
            return _console_with(*studio._console)

        studio.call = call
        identity = await registry.read_in_band_identity(studio)
        self.assertEqual(identity["debug_id"], "0_9")
        self.assertIn("get_console_output", seen)
        # The fallback is the one that prints.
        self.assertIn("print", studio.executed[-1]["code"])

    async def test_falls_back_when_the_return_channel_raises(self):
        studio = FakeStudio(["MCPID\t0_5\tAfterFailure\t1\t2"])
        executes = {"n": 0}

        async def call(name, arguments=None):
            if name == "execute_luau":
                executes["n"] += 1
                # Only the FIRST execute_luau (the return channel) fails; the
                # fallback's print must be allowed through.
                if executes["n"] == 1:
                    raise MCPToolError("return channel unavailable")
                return _console_with()
            return _console_with(*studio._console)

        studio.call = call
        identity = await registry.read_in_band_identity(studio)
        self.assertEqual(identity["debug_id"], "0_5")

    async def test_both_channels_agree_on_the_parse(self):
        """One parser, two channels. A tagged line parses the same either way,
        including when the tool wrapped it in its own quoting."""
        for text in ('MCPID\t0_1\tPlace1\t123\t456', '"MCPID\t0_1\tPlace1\t123\t456"'):
            with self.subTest(text=text):
                self.assertEqual(
                    registry._parse_identity(text),
                    {"debug_id": "0_1", "name": "Place1", "place_id": 123, "game_id": 456},
                )

    async def test_non_numeric_ids_are_zero_not_propagated(self):
        got = registry._parse_identity("MCPID\t0_1\tPlace1\tnope\talso-nope")
        self.assertEqual(got["place_id"], 0)
        self.assertEqual(got["game_id"], 0)

    async def test_a_truncated_line_is_not_an_identity(self):
        self.assertIsNone(registry._parse_identity("MCPID\t0_1\tOnlyTwo"))


class TestRecord(RegistryTestCase):
    def test_keys_by_debug_id(self):
        registry.record(
            studio_id="sid-1",
            identity={"debug_id": "0_1", "name": "A", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        self.assertIn("0_1", self.read()["instances"])

    def test_falls_back_to_a_session_key_without_identity(self):
        registry.record(studio_id="sid-9", identity=None, path=self.path)
        self.assertIn("session:sid-9", self.read()["instances"])

    def test_entries_unseen_for_30_days_are_dropped_on_record(self):
        """Every restart orphans its old entry (new debug_id, new key), so
        without pruning the file grows forever - measured 7 stored for 2 live.
        A 30-day-unseen entry is history no caller will ask about."""
        stale = {
            "debug_id": "0_old",
            "name": "Gone",
            "place_id": 0,
            "game_id": 0,
            "last_studio_id": "sid-old",
            "last_seen": time.time() - EXPIRE_AFTER_SECONDS - 60,
            "studio_id_history": ["sid-old"],
            "id_changed": False,
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "instances": {"0_old": stale}}, handle)
        registry.record(
            studio_id="sid-new",
            identity={"debug_id": "0_new", "name": "Here", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        instances = self.read()["instances"]
        self.assertNotIn("0_old", instances)
        self.assertIn("0_new", instances)

    def test_recent_entries_survive_a_record(self):
        registry.record(
            studio_id="sid-a",
            identity={"debug_id": "0_a", "name": "A", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        # Backdate just inside the window by rewriting, then record again.
        data = self.read()
        data["instances"]["0_a"]["last_seen"] = time.time() - 60
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        registry.record(
            studio_id="sid-b",
            identity={"debug_id": "0_b", "name": "B", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        instances = self.read()["instances"]
        self.assertIn("0_a", instances)
        self.assertIn("0_b", instances)

    def test_non_dict_entries_are_dropped_not_crashed_on(self):
        """_load explicitly contemplates hand edits and foreign versions, and
        record() runs on every refreshing list - so a truthy non-dict value
        must not raise: any non-object shape reads as infinitely old."""
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "instances": {"junk": [1, 2], "junk2": "x"}},
                handle,
            )
        registry.record(
            studio_id="sid-fresh",
            identity={"debug_id": "0_f", "name": "F", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        instances = self.read()["instances"]
        self.assertNotIn("junk", instances)
        self.assertNotIn("junk2", instances)
        self.assertIn("0_f", instances)

    def test_id_churn_is_recorded_under_a_stable_key(self):
        identity = {"debug_id": "0_1", "name": "A", "place_id": 0, "game_id": 0}
        registry.record(studio_id="old", identity=identity, path=self.path)
        entry = registry.record(studio_id="new", identity=identity, path=self.path)
        self.assertEqual(entry["debug_id"], "0_1")
        self.assertTrue(entry["id_changed"])
        self.assertEqual(entry["studio_id_history"], ["old", "new"])
        # still one entry: the identity did not change, only the proxy id
        self.assertEqual(len(self.read()["instances"]), 1)

    def test_preserves_an_unrelated_version(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 999, "instances": {"x": {}}}, handle)
        registry.record(
            studio_id="sid-1",
            identity={"debug_id": "0_1", "name": "A", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        # our write replaced it, but we must never have *deleted* a foreign file
        self.assertEqual(self.read()["version"], registry.REGISTRY_VERSION)

    def test_a_corrupt_file_does_not_crash(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        registry.record(
            studio_id="sid-1",
            identity={"debug_id": "0_1", "name": "A", "place_id": 0, "game_id": 0},
            path=self.path,
        )
        self.assertIn("0_1", self.read()["instances"])


class TestResolve(RegistryTestCase):
    def _seed(self, *pairs):
        for debug_id, name in pairs:
            registry.record(
                studio_id=f"sid-{debug_id}",
                identity={"debug_id": debug_id, "name": name, "place_id": 0, "game_id": 0},
                path=self.path,
            )

    def test_exact_debug_id(self):
        self._seed(("0_1", "A"), ("0_2", "B"))
        hit = registry.resolve(debug_id="0_2", path=self.path)
        self.assertEqual(hit["status"], "ok")
        self.assertEqual(hit["match"]["name"], "B")

    def test_unknown_debug_id(self):
        self._seed(("0_1", "A"))
        miss = registry.resolve(debug_id="nope", path=self.path)
        self.assertEqual(miss["status"], "not_found")
        self.assertIn("known", miss)

    def test_ambiguous_refuses_to_guess(self):
        self._seed(("0_1", "A"), ("0_2", "B"))
        everything = registry.resolve(path=self.path)
        self.assertEqual(everything["status"], "ambiguous")
        self.assertEqual(len(everything["candidates"]), 2)
        # must not have picked a winner
        self.assertNotIn("match", everything)

    def test_name_disambiguates(self):
        self._seed(("0_1", "A"), ("0_2", "B"))
        hit = registry.resolve(name="B", path=self.path)
        self.assertEqual(hit["status"], "ok")
        self.assertEqual(hit["match"]["debug_id"], "0_2")

    def test_duplicate_names_are_still_ambiguous(self):
        self._seed(("0_1", "Same"), ("0_2", "Same"))
        result = registry.resolve(name="Same", path=self.path)
        self.assertEqual(result["status"], "ambiguous")

    def test_stale_entries_are_flagged_not_hidden(self):
        self._seed(("0_1", "A"))
        with mock.patch.object(registry.time, "time", return_value=0.0):
            registry.record(
                studio_id="sid-old",
                identity={"debug_id": "0_1", "name": "A", "place_id": 0, "game_id": 0},
                path=self.path,
            )
        hit = registry.resolve(debug_id="0_1", path=self.path)
        self.assertIn("stale", hit["match"])
        self.assertEqual(hit["status"], "ok")


class TestListInstances(RegistryTestCase):
    async def test_merges_proxy_list_with_identity(self):
        client = FakeListClient(
            [{"id": "sid-1", "name": "A"}, {"id": "sid-2", "name": "B"}]
        )
        result = await registry.list_instances(client, path=self.path)
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            sorted(e["reported_name"] for e in result["instances"]), ["A", "B"]
        )
        self.assertTrue(all(e["registered"] for e in result["instances"]))
        self.assertEqual(result["registered_total"], 2)

    async def test_identity_failure_is_reported_not_fatal(self):
        client = FakeListClient([{"id": "sid-1", "name": "A"}])
        with mock.patch.object(
            registry, "read_in_band_identity", side_effect=MCPToolError("boom")
        ):
            result = await registry.list_instances(client, path=self.path)
        entry = result["instances"][0]
        self.assertEqual(entry["debug_id"], None)
        self.assertIn("boom", entry["identity_error"])
        # still listed, just without identity
        self.assertEqual(result["count"], 1)

    async def test_refresh_false_does_not_write(self):
        client = FakeListClient([{"id": "sid-1", "name": "A"}])
        await registry.list_instances(client, refresh=False, path=self.path)
        self.assertFalse(os.path.exists(self.path))


class TestRegistryPath(unittest.TestCase):
    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"ROBLOX_STUDIO_MCP_REGISTRY": "C:/tmp/x.json"}):
            # spelling-ok: registry_path returns the override verbatim
            # (no resolution), so this asserts passthrough, not a file.
            self.assertEqual(registry.registry_path(), "C:/tmp/x.json")

    def test_default_is_outside_the_project(self):
        # The invariant is "not inside this checkout", not "not under a path
        # that happens to read a certain way". An earlier version asserted
        # ``"Source" not in path``, which passed on the dev machine only
        # because of where that checkout sat - a home directory containing
        # "Source" would fail it while the code was right.
        root = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        )
        path = registry.registry_path()
        self.assertTrue(path.endswith(os.path.join("roblox-studio-mcp", "studios.json")))
        self.assertFalse(
            os.path.normcase(os.path.abspath(path)).startswith(
                os.path.normcase(root) + os.sep
            ),
            "default registry must not live inside the checkout: %r" % path,
        )


if __name__ == "__main__":
    unittest.main()
