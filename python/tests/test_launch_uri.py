"""Tests for the launch URI shape, universe resolution, and the command-line parsers.

The URI is the smallest thing in this project that can silently do the wrong
thing, because a failed URI launch still produces a process and still attaches
to the mesh. These tests pin the key count, that ``universeId`` is *always*
present, and the derivation rule: **a place's universe is a function of its
place id**, so ``universe_id`` defaults to ``None`` meaning "ask the API" rather
than to a number the caller must already know.

Nothing here touches the network. ``resolve_universe_id`` is driven through an
injected opener, and the retry behaviour is tested against a scripted sequence of
failures - including a negative control, because a retry test that cannot fail is
indistinguishable from one that passes.
"""

import asyncio
import json
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from unittest import mock

from roblox_studio_mcp.extended import instance as I
from roblox_studio_mcp.extended.instance import (
    ROLE_CLIENT, ROLE_EDIT, ROLE_SERVER, ROLE_UNKNOWN, URI_UNIVERSE_ID,
    UniverseLookupError, build_launch_uri, place_from_command_line,
    resolve_universe_id, role_from_command_line,
)

# The baseplate template, and the universe the API returns for it. Measured:
# GET /universes/v1/places/95206881/universe -> {"universeId": 28220420}
PLACE = 95206881
REAL_UNIVERSE = 28220420


def run(coro):
    """Drive one coroutine to completion.

    The suite uses ``IsolatedAsyncioTestCase`` elsewhere, but these tests are
    overwhelmingly synchronous shape checks that became async only because the
    default path performs I/O. Wrapping them keeps the diff to the tests that
    genuinely changed.
    """
    return asyncio.run(coro)


@contextmanager
def patched_urlopen(fn):
    """Swap ``urlopen`` for the duration of a block, then put it back.

    Hand-rolled save/restore got this wrong first time - the restore assigned
    the fake back onto itself, so every later test in the module inherited a
    patched ``urlopen`` and passed for the wrong reason. ``mock.patch`` restores
    by construction.
    """
    with mock.patch.object(urllib.request, "urlopen", fn):
        yield


class _Response:
    """Minimal stand-in for what ``urlopen`` returns as a context manager."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestLaunchUriShape(unittest.TestCase):
    """Everything that passes an explicit universe id stays pure in shape."""

    def test_exact_minimal_shape(self):
        self.assertEqual(
            run(build_launch_uri(PLACE, URI_UNIVERSE_ID)),
            f"roblox-studio:1+task:EditPlace+placeId:{PLACE}+universeId:0",
        )

    def test_the_real_universe_id_is_buildable(self):
        # Both values are measured to open the place: 8 of 8 launches on the real
        # id, 6 of 6 on 0, zero errors. The earlier claim that the real id "failed
        # 3 times in 16" did not reproduce and is withdrawn.
        self.assertEqual(
            run(build_launch_uri(PLACE, REAL_UNIVERSE)),
            f"roblox-studio:1+task:EditPlace+placeId:{PLACE}+universeId:{REAL_UNIVERSE}",
        )

    def test_no_optional_keys(self):
        # Every one of these appeared in a real Studio URI but is not required,
        # and adding them back produced an Error dialog that never attached.
        uri = run(build_launch_uri(1, 2))
        for absent in ("launchtime", "avatar", "browsertrackerid", "robloxLocale",
                       "gameLocale", "channel", "browser", "distributorType",
                       "baseUrl", "launchmode"):
            self.assertNotIn(absent, uri, f"{absent} must not be in the URI")

    def test_prefix_is_not_doubled(self):
        # Some samples show 'roblox-studio:roblox-studio:1+...' and that also
        # works, but it is an artifact of the protocol handler. Single is canonical.
        uri = run(build_launch_uri(1, 2))
        self.assertTrue(uri.startswith("roblox-studio:1+"))
        self.assertNotIn("roblox-studio:roblox-studio:", uri)

    def test_both_keys_are_always_present(self):
        # The key is present whatever the value. Dropping it left a Studio that
        # attached and opened no place, which is invisible unless you open it.
        for uid in (URI_UNIVERSE_ID, REAL_UNIVERSE):
            uri = run(build_launch_uri(PLACE, uid))
            self.assertIn("universeId", uri)
            self.assertIn("placeId", uri)

    def test_place_id_is_still_required(self):
        # Only the universe became optional; the place is the thing being opened.
        with self.assertRaises(TypeError):
            run(build_launch_uri())

    def test_ids_are_coerced_to_ints(self):
        uri = run(build_launch_uri("95206881", "28220420"))
        self.assertIn("placeId:95206881", uri)
        self.assertIn("universeId:28220420", uri)

    def test_zero_is_still_the_explicit_no_universe_value(self):
        # File > New opens a place as `-placeId N -universeId 0`, and a template
        # genuinely has no universe context, so 0 states something true.
        self.assertEqual(URI_UNIVERSE_ID, 0)
        self.assertIn("universeId:0", run(build_launch_uri(PLACE, URI_UNIVERSE_ID)))


class TestUniverseIsDerivedFromThePlace(unittest.TestCase):
    """`universe_id=None` means ask, and the answer is a function of the place."""

    def test_default_looks_the_universe_up(self):
        calls = []

        def fake(url, timeout=None):
            calls.append(url)
            return _Response({"universeId": REAL_UNIVERSE})

        with patched_urlopen(fake):
            uri = run(build_launch_uri(PLACE))

        self.assertEqual(
            uri,
            f"roblox-studio:1+task:EditPlace+placeId:{PLACE}"
            f"+universeId:{REAL_UNIVERSE}",
        )
        self.assertEqual(len(calls), 1, "the default must ask exactly once")
        self.assertIn(str(PLACE), calls[0])

    def test_an_explicit_value_never_hits_the_network(self):
        # Passing a universe is a promise not to ask. A launch path that
        # silently depended on the network would fail offline, and would report
        # a network failure as if it were a bad URI.
        def explode(*a, **k):
            raise AssertionError("the network was touched despite an explicit id")

        with patched_urlopen(explode):
            uri = run(build_launch_uri(PLACE, URI_UNIVERSE_ID))

        self.assertIn("universeId:0", uri)

    def test_a_failed_lookup_is_not_reported_as_a_bad_uri(self):
        # The failure mode this change could have introduced: a network problem
        # surfacing as a malformed-looking universe value. It raises instead.
        def down(url, timeout=None):
            raise urllib.error.URLError("offline")

        with patched_urlopen(down):
            with self.assertRaises(UniverseLookupError):
                run(build_launch_uri(PLACE))


class TestResolveUniverseId(unittest.TestCase):
    """The retry behaviour, driven by a scripted sequence rather than a socket."""

    def _run(self, outcomes, **kwargs):
        """Feed a sequence of outcomes to the resolver.

        Each outcome is a payload dict or an exception to raise. Returns
        (result_or_exception, attempts_made); raises on exhaustion.
        """
        seq = list(outcomes)
        attempts = []

        def fake(url, timeout=None):
            attempts.append(url)
            if not seq:
                raise AssertionError("resolver made more attempts than scripted")
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Response(item)

        with patched_urlopen(fake):
            result = run(resolve_universe_id(PLACE, delay=0, **kwargs))
        return result, len(attempts)

    def test_a_single_good_answer_needs_one_attempt(self):
        result, attempts = self._run([{"universeId": REAL_UNIVERSE}])
        self.assertEqual(result, REAL_UNIVERSE)
        self.assertEqual(attempts, 1)

    def test_a_transient_failure_is_retried_and_then_succeeds(self):
        # The reason retries default to 3: one fast call on a launch path turns a
        # network blip into "could not open the place", and the caller then looks
        # at the URI instead of at the network.
        result, attempts = self._run([
            urllib.error.URLError("connection reset"),
            urllib.error.HTTPError("u", 503, "unavailable", None, None),
            {"universeId": REAL_UNIVERSE},
        ])
        self.assertEqual(result, REAL_UNIVERSE)
        self.assertEqual(attempts, 3)

    def test_three_failures_raise_and_say_how_many_were_made(self):
        with self.assertRaises(UniverseLookupError) as ctx:
            self._run([urllib.error.URLError("down")] * 3)
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(ctx.exception.place_id, PLACE)
        self.assertIn("network error", ctx.exception.reason)

    def test_a_response_without_a_universe_id_is_a_failure_not_a_zero(self):
        # Coercing this to 0 would silently mean "no universe" - the exact
        # substitution this whole change exists to make impossible.
        with self.assertRaises(UniverseLookupError) as ctx:
            self._run([{"somethingElse": 1}] * 3)
        self.assertIn("no universeId", ctx.exception.reason)

    def test_malformed_json_is_a_failure(self):
        class R:
            def read(self):
                return b"<html>not json</html>"

            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

        with patched_urlopen(lambda url, timeout=None: R()):
            with self.assertRaises(UniverseLookupError):
                run(resolve_universe_id(PLACE, retries=2, delay=0))

    def test_retries_can_be_configured(self):
        with self.assertRaises(UniverseLookupError) as ctx:
            self._run([urllib.error.URLError("down")] * 5, retries=5)
        self.assertEqual(ctx.exception.attempts, 5)

    def test_the_retry_count_is_three_by_default(self):
        # Pinned because the request was "3 retries": a silent change to this
        # default would weaken the launch path with nothing failing.
        import inspect

        sig = inspect.signature(resolve_universe_id)
        self.assertEqual(sig.parameters["retries"].default, 3)

    def test_negative_control_retries_can_actually_be_observed(self):
        """A retry test that cannot fail proves nothing.

        The resolver is given exactly as many scripted failures as it will
        attempt. If it stopped retrying, the leftover script entries would go
        unconsumed - so the assertion is on the attempt count, which is the exact
        thing that would regress unnoticed.
        """
        seq = [urllib.error.URLError("a"), urllib.error.URLError("b"),
               urllib.error.URLError("c")]
        attempts = []

        def fake(url, timeout=None):
            attempts.append(url)
            raise seq.pop(0)

        with patched_urlopen(fake):
            with self.assertRaises(UniverseLookupError):
                run(resolve_universe_id(PLACE, delay=0))

        self.assertEqual(len(attempts), 3)
        self.assertEqual(seq, [], "the resolver stopped before using its script")

    def test_the_patch_does_not_leak(self):
        """Guards the mistake this module actually made once.

        The first version restored ``urlopen`` by assigning the fake onto itself,
        so every subsequent test inherited a patched ``urlopen`` and passed for
        the wrong reason. This asserts the real thing is back in place.
        """
        before = urllib.request.urlopen
        with patched_urlopen(lambda url, timeout=None: _Response({})):
            self.assertIsNot(urllib.request.urlopen, before)
        self.assertIs(urllib.request.urlopen, before)


class TestRoleParsing(unittest.TestCase):
    CASES = [
        ('"C:\\x\\RobloxStudioBeta.exe" --task EditFile --localPlaceFile C:\\a.rbxl', ROLE_EDIT),
        ('"C:\\x.exe" -task StartServer -localProjectFile C:/a.rbxl', ROLE_SERVER),
        ('"C:\\x.exe" -task StartClient -localProjectFile C:/a.rbxl', ROLE_CLIENT),
        ('"C:\\x.exe" roblox-studio:1+task:EditPlace+placeId:1+universeId:2', ROLE_EDIT),
        ('"C:\\x.exe" roblox-studio:1+task:StartServer+placeId:1+universeId:2', ROLE_SERVER),
        ('"C:\\x.exe" roblox-studio:1+task:StartClient+placeId:1+universeId:2', ROLE_CLIENT),
        ('"C:\\x.exe"', ROLE_UNKNOWN),
        ('', ROLE_UNKNOWN),
    ]

    def test_roles(self):
        for cmd, expected in self.CASES:
            with self.subTest(cmd=cmd[:60]):
                self.assertEqual(role_from_command_line(cmd), expected)

    def test_uri_launches_are_not_unknown(self):
        # A -task-only parse called every URI-launched Studio "unknown", which
        # is what hid the fact that a server and its clients were all identical.
        self.assertNotEqual(
            role_from_command_line('"x.exe" roblox-studio:1+task:EditPlace'), ROLE_UNKNOWN
        )


class TestPlaceParsing(unittest.TestCase):
    def test_file_launch_yields_a_basename(self):
        self.assertEqual(
            place_from_command_line('"x.exe" --task EditFile --localPlaceFile C:\\tmp\\Baseplate-1.rbxl'),
            "Baseplate-1.rbxl",
        )

    def test_project_file_form_also_works(self):
        self.assertEqual(
            place_from_command_line('"x.exe" -task StartServer -localProjectFile C:/tmp/B.rbxl'),
            "B.rbxl",
        )

    def test_uri_launch_has_no_derivable_place_name(self):
        # A URI carries only an id. Studio names the place itself, and while it
        # is still opening, the mesh reports name: null too.
        self.assertIsNone(
            place_from_command_line('"x.exe" roblox-studio:1+task:EditPlace+placeId:1+universeId:2')
        )


if __name__ == "__main__":
    unittest.main()