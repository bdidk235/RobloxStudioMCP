"""Python must match `contract/tools.json`.

This is the spec-conformance check: `contract/build_contract.py` generates the
contract from the Python server, and this test verifies the server still
conforms to it. The contract is the authority; a deliberate surface change
regenerates it, and anything else fails here.

What is compared, and why only this
----------------------------------
* **Tool names.** A caller must find the tools the contract promises.
* **Schema property names and types.** A parameter that exists in the contract
  and not in the server (or vice versa) is the worst kind of drift, because
  unknown parameters are **silently ignored** (request P0.2a) - so the call
  appears to work and returns something other than what was asked for.
* **Required parameters.** A required argument missing from the server turns a
  working call into a runtime failure.
* **Description length**, against both caps. The tool list is paid on every call
  of every session, so an unnoticed 400-character description is a real cost.

What is **not** compared: the description *wording*, and implementation
internals. Divergent prose is not a defect; a caller cannot observe it. Wording
is budgeted, not dictated.
"""

import json
import os
import sys
import unittest

# Three dirnames, not two: this file is python/tests/test_contract.py, so two
# would stop at `python/`. Getting this wrong made the import fail with a
# "missing dependency" message that pointed nowhere near the real cause.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))
sys.path.insert(0, _ROOT)  # so `contract.build_contract` imports as a package

from contract.build_contract import _normalise_schema, build  # noqa: E402
from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS  # noqa: E402

CONTRACT_PATH = os.path.join(_ROOT, "contract", "tools.json")

with open(CONTRACT_PATH, encoding="utf-8") as _handle:
    CONTRACT = json.load(_handle)

BY_NAME = {tool.name: tool for tool in _EXTENDED_TOOLS}


class SteersMatchTheGeneratedContract(unittest.TestCase):
    """The relay-to-extended steering table exists in three places.

    `contract/build_contract.py::_STEERS` is the source, the server keeps a copy
    to apply, and `contract/tools.json` ships a rendered third. This asserts the
    first two are identical - byte for byte, not "roughly equivalent"; the third
    is pinned by `RegenerationReproducesTheCommittedContract`. The failure mode
    it guards is specific and invisible: if the server copy drifts, a model is
    silently steered toward the worse tool, and every call still succeeds.
    Nothing else in the suite would notice, because the tools themselves are
    fine.
    """

    def _contract(self) -> dict:
        with open(CONTRACT_PATH, encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_contract_lists_the_steers(self):
        steers = self._contract()["steers"]
        self.assertTrue(steers, "steering table is empty - was it dropped?")
        for entry in steers:
            self.assertIn("name", entry)
            self.assertIn("prefer extended_", entry["note"], entry["name"])

    def test_python_matches_the_contract_exactly(self):
        from roblox_studio_mcp.extended_server import _STEERS

        expected = {e["name"]: e["note"] for e in self._contract()["steers"]}
        self.assertEqual(_STEERS, expected)

    def test_every_steer_names_a_tool_that_exists(self):
        """A steer pointing at a renamed tool is worse than none: it names a
        capability the caller cannot invoke."""
        from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS, _STEERS

        live = {t.name for t in _EXTENDED_TOOLS}
        for name, note in _STEERS.items():
            for token in note.replace(",", " ").split():
                if token.startswith("extended_"):
                    self.assertIn(token.strip(".;'"), live, f"{name} -> {token}")


class ContractIsCurrent(unittest.TestCase):
    def test_the_contract_matches_the_python_tools(self):
        """Regenerate with `python contract/build_contract.py` after a deliberate
        change; a failing test here means the surface moved without the contract
        following, which is exactly how drift goes unnoticed."""
        expected = sorted(entry["name"] for entry in CONTRACT["tools"])
        self.assertEqual(sorted(BY_NAME), expected)

    def test_the_contract_is_not_stale_on_size(self):
        self.assertEqual(CONTRACT["tool_count"], len(_EXTENDED_TOOLS))
        self.assertEqual(
            CONTRACT["total_description_chars"],
            sum(len(t.description) for t in _EXTENDED_TOOLS),
        )


class DescriptionBudget(unittest.TestCase):
    def test_every_tool_is_within_the_per_tool_cap(self):
        cap = CONTRACT["per_tool_description_cap"]
        for name, tool in sorted(BY_NAME.items()):
            self.assertLessEqual(len(tool.description), cap, name)

    def test_the_total_is_within_the_cap(self):
        """The binding constraint. 303 characters of headroom at last measure, so
        this is the test that makes a new tool a deliberate decision rather than
        an accident."""
        total = sum(len(t.description) for t in _EXTENDED_TOOLS)
        self.assertLessEqual(
            total, CONTRACT["total_description_cap"],
            "descriptions total %d, cap %d" % (total, CONTRACT["total_description_cap"]),
        )


class SchemaContract(unittest.TestCase):
    def _entry(self, name):
        for entry in CONTRACT["tools"]:
            if entry["name"] == name:
                return entry
        self.fail("no contract entry for %s" % name)

    def test_every_tool_in_the_contract_exists_here(self):
        for entry in CONTRACT["tools"]:
            self.assertIn(entry["name"], BY_NAME, entry["name"])

    def test_property_sets_match_the_contract(self):
        for entry in CONTRACT["tools"]:
            got = set(BY_NAME[entry["name"]].input_schema.get("properties") or {})
            want = set(entry["schema"]["properties"])
            self.assertEqual(
                got, want,
                "%s: properties differ from the contract" % entry["name"],
            )

    def test_required_lists_match_the_contract(self):
        for entry in CONTRACT["tools"]:
            got = sorted(BY_NAME[entry["name"]].input_schema.get("required") or [])
            self.assertEqual(
                got, entry["schema"]["required"],
                "%s: required differs from the contract" % entry["name"],
            )

    def test_property_types_match_the_contract(self):
        for entry in CONTRACT["tools"]:
            normalised = _normalise_schema(BY_NAME[entry["name"]].input_schema)
            self.assertEqual(
                normalised["properties"], entry["schema"]["properties"],
                "%s: property types differ" % entry["name"],
            )

    def test_no_required_argument_is_shadowed_by_a_default(self):
        """A required argument that also carries a default is a default in
        disguise: the caller is told it is required, and the server invents a
        value if it is missing. Rejected here rather than left to judgement."""
        for name, tool in sorted(BY_NAME.items()):
            schema = tool.input_schema
            required = set(schema.get("required") or [])
            for prop in required & set(schema.get("properties") or {}):
                spec = schema["properties"][prop]
                self.assertNotIn(
                    "default", spec,
                    "%s: required argument %r also has a default" % (name, prop),
                )

    def test_read_only_hints_match_the_contract(self):
        """Checked from both ends, because it once drifted in one direction only.

        The hint tells a client a tool only observes, so it may run those in
        parallel. Unmarked reads as "may mutate", so a missing hint is *safe* -
        which is exactly why an unmarked set went unnoticed in the past.
        Recording it in the contract turned that from an unremarked difference
        into a failing assertion.
        """
        for entry in CONTRACT["tools"]:
            self.assertEqual(
                bool(getattr(BY_NAME[entry["name"]], "read_only", False)),
                bool(entry["read_only"]),
                "%s: read_only differs from the contract" % entry["name"],
            )

    def test_the_read_only_set_names_real_tools(self):
        """A name in the set that matches no tool is a rename left half-done."""
        from roblox_studio_mcp.extended_server import _READONLY_TOOLS

        self.assertEqual(_READONLY_TOOLS - set(BY_NAME), set())

    def test_mutating_tools_are_not_marked_read_only(self):
        """The hint grants parallel execution, so marking a writer read-only
        would be the dangerous direction. Asserted by behaviour, not by list:
        the two tools that only *sometimes* observe are deliberately unmarked."""
        for name in ("extended_write_script", "extended_update_script",
                     "extended_manage_instance", "extended_wait_for",
                     "extended_breakpoints"):
            self.assertFalse(
                getattr(BY_NAME[name], "read_only", False),
                "%s can mutate or execute caller code and must not be marked" % name,
            )


class RegenerationReproducesTheCommittedContract(unittest.TestCase):
    """The committed file must equal a fresh run of the generator.

    `contract/build_contract.py:10-11` claims the contract "cannot drift without
    a test failing". That was aspirational: `build()` was called from `main()`
    and nowhere else, so every test read `tools.json` off disk and compared the
    *server* against it. The generator's own inputs were unpinned - editing
    `_STEERS` at `build_contract.py:67` moved neither side of that comparison,
    and the suite stayed green while the generator and the committed file
    disagreed.

    `build()` is pure, so this is a regeneration diff rather than a second
    comparison, and it makes the `:10-11` claim true instead of asserted.

    If this fails on a tree you have not touched, **clear
    `contract/__pycache__` before believing the diff.** CPython trusts a `.pyc`
    whose header mtime-and-size match the source, so a stale cache survives an
    edit that changed a string's case but not the file's length. Measured while
    this test was written: a cached `build()` emitted `EXTENDED_write_script`
    where the source and the committed contract both say `extended_write_script`
    - same byte length, so the header matched and the cache was accepted. The
    committed contract was correct; the cache was the liar.
    """

    def test_a_fresh_build_reproduces_the_committed_file(self):
        fresh = json.dumps(build(), indent=2, sort_keys=False) + "\n"
        with open(CONTRACT_PATH, encoding="utf-8") as handle:
            committed = handle.read()
        self.assertEqual(
            committed,
            fresh,
            "contract/tools.json is not what build() produces now. Regenerate "
            "with `python contract/build_contract.py`, or the generator and the "
            "contract it ships have diverged.",
        )


if __name__ == "__main__":
    unittest.main()
