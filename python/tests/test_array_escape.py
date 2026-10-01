"""The array-escape detector: what it must flag, and what it must never touch.

`execute_luau` is a relayed Studio tool and Studio's serialiser stringifies
integer keys, so a Luau array arrives as an object with keys "1","2",... Measured
live; see `REQUEST-luau-return-shapes.md` and the `rsx-transport` skill.

The detector exists because a 165-row driver returned `{}` with no error. But the
interesting half is the negative cases, because **the shape is ambiguous**:

* `{[1]=x,[2]=y}` is a genuine array and should have been a list
* `{["1"]=x,["2"]=y}` is a genuine string-keyed map and is already correct

Identical bytes. So this must never *repair* anything - it reports, and the
payload the caller reads is untouched. A "helpful" rebuild here would silently
corrupt every real string-keyed table, which is a worse bug than the one being
fixed.
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Dict, List

from roblox_studio_mcp.extended.extensions import (
    ARRAY_ESCAPE_NOTE,
    _annotate_array_escape,
    _array_escape_paths,
)
from roblox_studio_mcp.types import CallToolResult


def _result(value: Any) -> CallToolResult:
    return CallToolResult(content=[{"type": "text", "text": json.dumps(value)}])


class FlagsTheMeasuredShapes(unittest.TestCase):
    """The three rows measured live against a real Studio."""

    def test_a_flat_array(self):
        self.assertEqual(_array_escape_paths({"1": 10, "2": 20, "3": 30}), ["(root)"])

    def test_an_array_nested_under_a_key(self):
        got = _array_escape_paths({"rows": {"1": {"n": 1}, "2": {"n": 2}}})
        self.assertEqual(got, ["rows"])

    def test_the_deeply_nested_case_from_the_report(self):
        got = _array_escape_paths(
            {"rows": {"1": {"n": 1, "v": 10}, "2": {"n": 2, "v": 20}}}
        )
        self.assertEqual(got, ["rows"])

    def test_several_arrays_are_all_reported(self):
        got = _array_escape_paths({"a": {"1": 1, "2": 2}, "b": {"1": 3, "2": 4}})
        self.assertEqual(sorted(got), ["a", "b"])

    def test_real_int_keys_must_not_crash_the_detector(self):
        # A dict built in-process can carry real `int` keys. Over JSON the
        # keys are always strings, so only an in-process caller reaches this -
        # but `k.isdigit()` on an `int` is an AttributeError that escaped this
        # function and would have failed the whole tool call. Pinned because a
        # passing test suite once hid exactly that crash.
        self.assertEqual(_array_escape_paths({1: 10, 2: 20}), ["(root)"])

    def test_an_array_inside_a_single_keyed_object_reports_the_indexed_path(self):
        # `(root)` applies only when the dense object IS the root. Here the
        # root has a single key "1", so the path starts at that key - and Node
        # must name it the same way (see `agrees with Python` over there).
        self.assertEqual(
            _array_escape_paths({1: [{1: 1, 2: 2}]}),
            ["1[0]"],
        )


class DoesNotFlagWhatItMustNot(unittest.TestCase):
    """Every one of these produced a false positive at some point in a draft."""

    def test_a_sparse_numeric_key_set_is_not_dense(self):
        self.assertEqual(_array_escape_paths({"1": "x", "3": "y"}), [])

    def test_numeric_keys_mixed_with_real_keys(self):
        self.assertEqual(_array_escape_paths({"1": "x", "name": "y"}), [])

    def test_a_single_element_table_is_too_weak_to_call(self):
        # n >= 2: one numeric key is far more likely a real key called "1".
        self.assertEqual(_array_escape_paths({"1": "x"}), [])

    def test_an_ordinary_object(self):
        self.assertEqual(_array_escape_paths({"a": 1, "b": 2}), [])

    def test_the_Vector2_case_which_arrives_as_a_scalar_string(self):
        """`Vector2.new(3,4)` arrives as `"3, 4"` - no keys at all, so there is
        nothing for this detector to see. Recorded because it is the worst case
        and the one the note has to mention."""
        self.assertEqual(_array_escape_paths({"p": "3, 4"}), [])

    def test_the_JSONEncode_workaround_must_never_be_flagged(self):
        self.assertEqual(_array_escape_paths({"json": "[10,20,30]"}), [])

    def test_scalars_and_none(self):
        self.assertEqual(_array_escape_paths(None), [])
        self.assertEqual(_array_escape_paths(7), [])
        self.assertEqual(_array_escape_paths("plain"), [])


class TheAmbiguityIsDocumented(unittest.TestCase):
    """The reason this reports instead of repairing, pinned as a test."""

    def test_the_flagged_shape_is_genuinely_both_things(self):
        # Written as a Lua array it should have been [x, y]. Written as a map
        # with string keys it is already correct. Same bytes, either way.
        shape: Dict[str, Any] = {"1": "x", "2": "y"}
        self.assertEqual(_array_escape_paths(shape), ["(root)"])
        self.assertEqual(_array_escape_paths(dict(shape)), ["(root)"])


class AnnotationIsAdditive(unittest.TestCase):
    """The payload must come back byte-identical. That is the whole contract."""

    def _payload(self, value: Any) -> List[Any]:
        return [dict(b) if isinstance(b, dict) else b for b in _result(value).content]

    def test_the_payload_is_unchanged(self):
        original: Dict[str, Any] = {"rows": {"1": {"n": 1}, "2": {"n": 2}}}
        before = self._payload(original)
        _annotate_array_escape(_result(original))
        after = self._payload(original)
        self.assertEqual(json.loads(before[0]["text"]), json.loads(after[0]["text"]))

    def test_a_note_is_appended_when_the_shape_is_present(self):
        result = _annotate_array_escape(_result({"rows": {"1": {"n": 1}, "2": {"n": 2}}}))
        self.assertEqual(len(result.content), 2)
        self.assertIn("do not survive", result.content[1]["text"])
        self.assertIn("rows", result.content[1]["text"])

    def test_no_note_when_there_is_nothing_to_say(self):
        result = _annotate_array_escape(_result({"json": "[10,20,30]"}))
        self.assertEqual(len(result.content), 1)

    def test_the_note_says_it_did_not_repair_anything(self):
        """A caller reading this must not think the data was fixed."""
        result = _annotate_array_escape(_result({"1": 1, "2": 2}))
        text = result.content[1]["text"]
        self.assertIn("nothing was rewritten", text)
        self.assertIn("HttpService:JSONEncode", text)

    def test_the_note_names_the_vector2_trap_too(self):
        self.assertIn("3, 4", ARRAY_ESCAPE_NOTE)

    def test_unparseable_content_is_left_alone(self):
        result = CallToolResult(content=[{"type": "text", "text": "not json at all"}])
        self.assertEqual(len(_annotate_array_escape(result).content), 1)

    def test_many_paths_are_summarised_not_dumped(self):
        value: Dict[str, Any] = {("k%d" % i): {"1": 1, "2": 2} for i in range(9)}
        result = _annotate_array_escape(_result(value))
        self.assertIn("+4 more", result.content[1]["text"])


if __name__ == "__main__":
    unittest.main()