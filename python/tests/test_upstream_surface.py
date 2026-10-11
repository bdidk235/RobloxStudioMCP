"""The upstream (relayed) tool surface is pinned, and graded not asserted.

Why this test exists
--------------------
`contract/tools.json` pins the ``extended_*`` tools this repo owns. The tools
the Studio MCP proxy *relays* were pinned nowhere until 2026-10-08: a live
``tools/list`` returned them, `docs/catalog.js` hand-carried them for the docs
site, and nothing asserted either against anything. The failure mode is the
worst kind - a Studio update that renames or drops a relayed tool breaks every
caller at runtime, in a user's session, with no CI run ever having seen it.

So `contract/upstream_tools.json` is a generated snapshot of that surface, and
this suite holds three things in place:

1. **The snapshot cannot be hand-edited or silently diverge from its
   generator.** `contract/upstream_live_dump.json` is the raw capture the
   snapshot was built from; a fresh `build_snapshot()` over that dump must
   reproduce the committed file byte for byte. This is the fix for the class of
   bug `test_contract.py::RegenerationReproducesTheCommittedContract`
   documents: without pinning the generator's *inputs*, the generator and the
   file it ships can disagree while every comparison stays green.
2. **The two trust labels stay separate.** The upstream surface is Roblox
   Studio's and changes without us; the extended surface is this repo's and is
   asserted byte-for-byte elsewhere. Mixing them under one trust label is how
   "28 tools, all fine" got said while nothing checked either half.
3. **A live comparison grades drift rather than demanding equality.** See
   `roblox_studio_mcp.upstream.compare_snapshot`: a single missing tool fails
   (that is the silent break), a mass disappearance only warns (that is a
   proxy that failed to enumerate, and CI must not redden on it), additions
   and reworded descriptions warn, parameter removals fail.
"""

import json
import os
import sys
import unittest

# Three dirnames, not two: this file is python/tests/test_upstream_surface.py.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))
sys.path.insert(0, _ROOT)  # so `contract.build_upstream` imports as a package

from contract.build_upstream import SNAPSHOT_PATH  # noqa: E402
from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS  # noqa: E402
from roblox_studio_mcp.upstream import (  # noqa: E402
    TRUST_UPSTREAM,
    build_snapshot,
    compare_snapshot,
    normalise_tool,
    snapshot_names,
)
from contract.build_contract import _STEERS as CONTRACT_STEERS  # noqa: E402
from roblox_studio_mcp.extended_server import _STEERS as SERVER_STEERS  # noqa: E402

DUMP_PATH = os.path.join(_ROOT, "contract", "upstream_live_dump.json")
CATALOG_PATH = os.path.join(_ROOT, "docs", "catalog.js")

EXTENDED_NAMES = {t.name for t in _EXTENDED_TOOLS}


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class SnapshotIsPinned(unittest.TestCase):
    """The snapshot is a generated file: its inputs are checked in, and the
    committed output must be exactly what the generator produces from them."""

    @classmethod
    def setUpClass(cls):
        cls.dump = _read_json(DUMP_PATH)
        with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
            cls.committed_text = handle.read()
        cls.snapshot = json.loads(cls.committed_text)

    def test_the_dump_is_a_tools_list_capture(self):
        tools = self.dump.get("tools")
        self.assertIsInstance(tools, list)
        self.assertTrue(tools, "the checked-in dump carries no tools")
        for tool in tools:
            self.assertIn("name", tool)
            self.assertTrue(tool["name"])
        self.assertTrue(self.dump.get("captured"), "the dump has no capture date")

    def test_a_fresh_build_reproduces_the_committed_snapshot(self):
        """Same contract as `test_contract.py`: regenerate and compare.

        A hand-edited snapshot fails here, and so does an edit to
        `build_snapshot` (or to `normalise_tool`) that the dump was not
        re-captured for. Both directions matter: the file cannot be lied
        about, and the code cannot be changed under it.
        """
        fresh = json.dumps(build_snapshot(self.dump), indent=2, sort_keys=False) + "\n"
        self.assertEqual(
            self.committed_text,
            fresh,
            "contract/upstream_tools.json is not what build_snapshot() produces "
            "from contract/upstream_live_dump.json. Re-run `python "
            "contract/build_upstream.py --live` after a Studio update, or "
            "`--from contract/upstream_live_dump.json` to rebuild offline.",
        )

    def test_the_snapshot_declares_the_upstream_trust_label(self):
        self.assertEqual(self.snapshot.get("trust"), TRUST_UPSTREAM)

    def test_the_snapshot_count_matches_its_tools(self):
        names = snapshot_names(self.snapshot)
        self.assertEqual(self.snapshot.get("tool_count"), len(names))
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")

    def test_every_pinned_tool_carries_a_comparable_shape(self):
        for tool in self.snapshot.get("tools") or []:
            self.assertIn("description_chars", tool)
            self.assertIsInstance(tool.get("required"), list)
            self.assertIsInstance(tool.get("properties"), dict)

    def test_normalise_accepts_a_wire_dict_and_a_tool_dict(self):
        """The doctor feeds `Tool.to_dict()` output; the refresh script feeds
        the raw wire payload. Both must normalise identically."""
        wire = {
            "name": "wire_tool",
            "description": "abc",
            "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}},
                            "required": ["x"]},
        }
        self.assertEqual(normalise_tool(wire), normalise_tool(wire))
        self.assertEqual(
            normalise_tool(wire),
            {
                "name": "wire_tool",
                "description_chars": 3,
                "required": ["x"],
                "properties": {"x": {"type": "string"}},
            },
        )
        same = dict(wire, annotations={"readOnlyHint": True})
        self.assertTrue(normalise_tool(same)["read_only"])
        self.assertNotIn("read_only", normalise_tool(wire))


class TheTwoTrustLabelsStaySeparate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = _read_json(SNAPSHOT_PATH)

    def test_no_tool_is_both_extended_and_upstream(self):
        overlap = set(snapshot_names(self.snapshot)) & EXTENDED_NAMES
        self.assertEqual(
            overlap,
            set(),
            "a name in both surfaces shares one trust label - upstream is "
            "Studio's and graded; extended is this repo's and asserted",
        )

    def test_every_steer_names_a_pinned_upstream_tool(self):
        """A steer pointing at a tool upstream no longer ships is worse than
        none: it names a capability the caller cannot invoke. With the
        snapshot pinned, an upstream rename turns this red instead of leaving
        the note quietly wrong - and `test_contract.py` already pins the other
        end, that each steer's note names an existing extended tool."""
        pinned = set(snapshot_names(self.snapshot))
        # The two tables spell the same mapping differently (tuple-of-pairs
        # vs dict); compare names, not shapes - the shape is each file's own
        # business and the test before this one already pins the wiring.
        contract_names = {name for name, _note in CONTRACT_STEERS}
        self.assertEqual(contract_names, set(SERVER_STEERS.keys()))
        for name in contract_names:
            self.assertIn(
                name,
                pinned,
                "the steer for %r names an upstream tool the snapshot does "
                "not carry - refresh the snapshot, or drop the steer" % name,
            )

    def test_the_docs_catalog_covers_the_pinned_surface(self):
        """`docs/catalog.js` is hand-maintained for the docs site. It matched
        the live surface exactly on 2026-10-08 - unasserted, by luck. It is
        not generated, so this asserts the cheap invariant instead: every
        pinned upstream tool appears in it. Wording and req/opt lists stay the
        site's own business."""
        with open(CATALOG_PATH, encoding="utf-8") as handle:
            catalog = handle.read()
        missing = [n for n in snapshot_names(self.snapshot) if n not in catalog]
        self.assertEqual(
            missing,
            [],
            "pinned upstream tools absent from docs/catalog.js: %s" % missing,
        )


class GradingPolicy(unittest.TestCase):
    """The live comparison grades: one missing tool fails, a flaky proxy
    warns. Both directions are pinned here so neither can silently swap."""

    @classmethod
    def setUpClass(cls):
        cls.dump = _read_json(DUMP_PATH)
        cls.snapshot = build_snapshot(cls.dump)
        cls.tools = list(cls.dump.get("tools") or [])
        cls.first = cls.tools[0]["name"]

    def _compare(self, live):
        return compare_snapshot(self.snapshot, live)

    def _raw_named(self, name):
        """The raw dump entry for a snapshot tool, by name.

        The snapshot is sorted and the dump is in wire order, so `[0]` on one
        side is a different tool from `[0]` on the other. Every mutation test
        below resolves by name, or it mutates one tool and asserts about
        another - which is how two of them failed without a single real
        disagreement.
        """
        return json.loads(
            json.dumps(next(t for t in self.tools if t["name"] == name))
        )

    def test_the_capture_itself_is_clean(self):
        report = self._compare(self.tools)
        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.exit_code, 0)

    def test_a_single_missing_tool_fails(self):
        live = [t for t in self.tools if t["name"] != self.first]
        report = self._compare(live)
        kinds = {(f.level, f.kind, f.name) for f in report.findings}
        self.assertIn(("FAIL", "missing", self.first), kinds)
        self.assertEqual(report.exit_code, 1)

    def test_a_mass_disappearance_only_warns(self):
        """A proxy that failed to enumerate reports nothing, or nearly
        nothing. Reddening CI on that teaches people to ignore the check, so
        everything is downgraded to a warning and the report says why."""
        live = self.tools[:2]
        report = self._compare(live)
        self.assertEqual(report.failures, [], report.render())
        self.assertGreater(len(report.warnings), 2, report.render())
        self.assertEqual(report.exit_code, 0)
        self.assertTrue(any("failed to enumerate" in f.detail for f in report.warnings))

    def test_an_empty_live_list_warns_and_does_not_fail(self):
        report = self._compare([])
        self.assertEqual(report.failures, [], report.render())
        self.assertEqual(report.exit_code, 0)

    def test_an_added_tool_warns(self):
        live = list(self.tools) + [
            {"name": "brand_new", "description": "d", "inputSchema": {}}
        ]
        report = self._compare(live)
        kinds = {(f.level, f.kind) for f in report.findings}
        self.assertIn(("WARNING", "new"), kinds)
        self.assertEqual(report.exit_code, 0)

    def test_a_removed_parameter_fails(self):
        """The silent-failure class: a caller built against the snapshot
        passes a parameter that no longer exists."""
        tool = dict(self.tools[0])
        tool["inputSchema"] = {"type": "object", "properties": {}}
        live = [tool] + self.tools[1:]
        report = self._compare(live)
        kinds = {f.kind for f in report.failures}
        self.assertIn("property-removed", kinds)
        self.assertEqual(report.exit_code, 1)

    def test_a_changed_required_set_fails(self):
        tool = json.loads(json.dumps(self.tools[0]))
        tool["inputSchema"]["required"] = list(
            tool["inputSchema"].get("required") or []
        ) + ["extra"]
        live = [tool] + self.tools[1:]
        report = self._compare(live)
        self.assertIn("required-changed", {f.kind for f in report.failures})

    def test_a_changed_property_type_fails(self):
        target = self.snapshot["tools"][0]["name"]
        props = self.snapshot["tools"][0]["properties"]
        if not props:
            self.skipTest("the first pinned tool declares no properties")
        name = sorted(props)[0]
        tool = self._raw_named(target)
        tool["inputSchema"]["properties"][name]["type"] = "integer"
        live = [tool if t["name"] == target else t for t in self.tools]
        report = self._compare(live)
        self.assertIn("property-changed", {f.kind for f in report.failures})

    def test_a_reworded_description_warns(self):
        tool = dict(self.tools[0])
        tool = json.loads(json.dumps(tool))
        tool["description"] = (tool.get("description") or "") + " more words"
        live = [tool] + self.tools[1:]
        report = self._compare(live)
        kinds = {(f.level, f.kind) for f in report.findings}
        self.assertIn(("WARNING", "description-length"), kinds)
        self.assertEqual(report.exit_code, 0)

    def test_a_flipped_read_only_hint_warns(self):
        target = self.snapshot["tools"][0]["name"]
        props = self.snapshot["tools"][0]
        tool = self._raw_named(target)
        if props.get("read_only"):
            tool.pop("annotations", None)
        else:
            tool["annotations"] = {"readOnlyHint": True}
        live = [tool if t["name"] == target else t for t in self.tools]
        report = self._compare(live)
        kinds = {(f.level, f.kind) for f in report.findings}
        self.assertIn(("WARNING", "read-only-flipped"), kinds)
        self.assertEqual(report.exit_code, 0)

    def test_an_added_parameter_warns(self):
        props = self.snapshot["tools"][0]["properties"]
        tool = json.loads(json.dumps(self.tools[0]))
        if props:
            name = sorted(props)[0]
        else:
            name = "anything"
            tool["inputSchema"]["properties"] = {}
        tool["inputSchema"]["properties"][name + "_v2"] = {"type": "string"}
        live = [tool] + self.tools[1:]
        report = self._compare(live)
        kinds = {(f.level, f.kind) for f in report.findings}
        self.assertIn(("WARNING", "property-added"), kinds)
        self.assertEqual(report.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
