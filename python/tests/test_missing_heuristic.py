"""Pin the missing-script heuristic, including what it must NOT match.

The bare hints "nil" and "unknown" were measured misclassifying 5 of 7 unrelated
Studio errors as "missing". Under `create_if_missing` that silently creates a
blank script and reports "created" - so the negative cases here are the ones that
matter, and they are exactly the errors this project has catalogued as common.

A heuristic like this rots quietly: widening it looks harmless and every test
still passes, because the genuine misses keep matching. So the negative list is
the test.
"""

import unittest

from roblox_studio_mcp.extended.writer import _is_missing_error


class TestMissingScriptHeuristic(unittest.TestCase):
    GENUINE = [
        "HttpError: 404 Not Found",
        "the requested model does not exist",
        "no such child",
        "could not find module 'Foo'",
        "Model not found in workspace",
        "Script 'Foo' is missing from ServerScriptService",
    ]

    # None of these are lookup failures. "attempt to index nil" is the most common
    # Luau error there is, so it must never read as "the script is absent".
    UNRELATED = [
        "attempt to index nil (field 'ClientOnlyModules')",
        "attempt to call a nil value (method 'GetService')",
        "unknown user id",
        "unknown error: connection reset",
        "Cannot connect to server: nil",
        "ReplicatedStorage.Modules is not a valid member of Folder",
        "Server took 0.165s to load!",
        "Failed to upload TexturePack: HTTP 429",
    ]

    def test_genuine_misses_are_recognised(self):
        for msg in self.GENUINE:
            with self.subTest(msg=msg):
                self.assertTrue(_is_missing_error(Exception(msg)))

    def test_unrelated_errors_are_not_read_as_missing(self):
        for msg in self.UNRELATED:
            with self.subTest(msg=msg):
                self.assertFalse(
                    _is_missing_error(Exception(msg)),
                    f"{msg!r} was misread as a missing script; under "
                    "create_if_missing that creates a blank script and "
                    "reports 'created'",
                )

    def test_the_two_offending_bare_words_are_gone(self):
        # Pinned so the list cannot be widened back. A substring match on "nil"
        # or "unknown" is indistinguishable from matching arbitrary prose.
        from roblox_studio_mcp.extended.writer import _MISSING_HINTS

        for hint in ("nil", "unknown", "missing"):
            self.assertNotIn(
                hint, _MISSING_HINTS,
                f"{hint!r} is too broad to substring-match on",
            )

    def test_negative_control_the_check_can_still_say_yes(self):
        """A heuristic whose negatives all pass proves nothing if it is inert."""
        self.assertTrue(_is_missing_error(Exception("404 Not Found")))
        self.assertFalse(_is_missing_error(Exception("no error text here")))