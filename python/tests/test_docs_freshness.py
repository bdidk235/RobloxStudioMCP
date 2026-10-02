"""Fail when hand-written documentation contradicts the generated contract.

THE PROBLEM THIS EXISTS FOR

Every gate in this repo derives its thresholds from `parity/tools.json`, so the
*enforcement* cannot drift. The *documentation beside it* did, three times:

  - the total cap in `AGENTS.md` survived a raise and kept naming the old value
  - `TODO.md` quoted "7 characters" of headroom
  - `TODO.md` quoted "8 characters" of headroom, **in the adjacent bullet**

So a contributor reading `AGENTS.md` believed there was no room for another tool
when there was room for roughly two. Nothing failed, because nothing checked.

WHY IT ENCODES NO NUMBER

The obvious gate - assert the prose quotes the live figure - has a flaw: it
encodes the number, and would rot the same way on the next cap change. Worse, it
contradicts the rule the section itself states, which is that no figure should be
written there at all.

So this asserts the *rule*, not the value: a cap-like figure must not appear in
the normative section. That test cannot drift, because it contains no threshold
of its own. The figures live in one place, `parity/tools.json`, which is
generated.

`TODO.md` is deliberately exempt. It is a dated evidence log; recording what a
number *was* is its job, and rewriting history there would destroy the record.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "AGENTS.md"
CONTRACT = REPO / "parity" / "tools.json"

SECTION_HEADING = "### The tool list is budgeted"

# A figure that looks like a description budget: 2,000-9,999, with or without a
# thousands separator. Deliberately a *range*, not the real cap, so this test has
# no knowledge of the value it is protecting.
CAP_LIKE = re.compile(r"\b\d{1,2},\d{3}\b|\b[2-9]\d{3}\b")

# Headroom was quoted as a small integer next to a unit - "7 characters", "8
# characters", "11 spare" - and those figures rotted just as badly as the cap did.
# The negative control caught this: a four-digit-only detector passes straight
# over "there are 7 spare", which is one of the exact sentences that misled a
# reader. So small integers are caught too, but only when a unit word makes them
# a budget figure rather than a count of tools, dates, or rule numbers.
HEADROOM_LIKE = re.compile(
    r"\b\d{1,3}\s+(?:characters|chars|spare|headroom|to spare)\b", re.I
)


def budget_section(text: str) -> str:
    """The normative budget section, up to the next heading of the same level."""
    start = text.find(SECTION_HEADING)
    if start < 0:
        raise AssertionError(f"{SECTION_HEADING} not found in AGENTS.md")
    rest = text[start + len(SECTION_HEADING):]
    nxt = re.search(r"^#{2,3} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


class TestDocsFreshness(unittest.TestCase):
    def setUp(self):
        self.text = AGENTS.read_text(encoding="utf-8")
        self.section = budget_section(self.text)

    def test_the_budget_section_exists(self):
        # If this fails, every other test here is passing vacuously.
        self.assertGreater(len(self.section.strip()), 200)

    def test_the_budget_section_quotes_no_cap_like_figure(self):
        found = CAP_LIKE.findall(self.section) + HEADROOM_LIKE.findall(self.section)
        self.assertEqual(
            found, [],
            "AGENTS.md's budget section quotes %s. Figures belong in "
            "parity/tools.json, which is generated - the prose is what drifted "
            "three times already. Point at the contract instead."
            % (found or ""),
        )

    def test_the_section_points_at_the_generated_contract(self):
        self.assertIn("parity/tools.json", self.section)

    def test_the_contract_is_readable_and_carries_the_keys(self):
        # Guards the gate itself: if tools.json changes shape, the references
        # this section tells a reader to follow would be dead.
        import json

        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for key in ("total_description_cap", "per_tool_description_cap",
                    "total_description_chars", "tool_count"):
            self.assertIn(key, contract, f"{key} missing from the contract")

    def test_the_detector_can_actually_fail(self):
        """Negative control.

        A freshness check that cannot fail is indistinguishable from one that
        passes. `build-freshness.test.ts` sets the precedent in this repo, and
        the cost of skipping it is a green gate that watches nothing - which is
        the failure this whole file is about.

        Three separate poisoned sentences, one per rot that actually happened, so
        a future edit that narrows the detector fails here rather than silently
        ceasing to guard anything.
        """
        cases = {
            "survived a cap raise": "The cap is 2,700 characters total.\n",
            "headroom quoted as an integer":
                "There are 8 characters of headroom on the Node side.\n",
            "headroom quoted as spare": "Only 11 spare, so the list is full.\n",
        }
        for label, poison in cases.items():
            with self.subTest(rot=label):
                text = self.section + "\n" + poison
                found = (CAP_LIKE.findall(text)
                         + HEADROOM_LIKE.findall(text))
                self.assertTrue(
                    found, f"detector missed an injected figure: {poison.strip()}")

        # And it must stay quiet on the clean section, or the gate is noise.
        self.assertEqual(CAP_LIKE.findall(self.section), [])
        self.assertEqual(HEADROOM_LIKE.findall(self.section), [])


class TestParityTestReadsItsOwnCap(unittest.TestCase):
    """The enforcement side of the same asymmetry, pinned.

    `test_parity.py` must take its thresholds from the contract rather than
    hardcoding them. That is why the drift above was documentation-only, and it
    is worth a test because a hardcoded cap would move the failure into the gate,
    where it is silent.
    """

    def test_parity_test_does_not_hardcode_a_cap(self):
        src = (REPO / "python" / "tests" / "test_parity.py").read_text(
            encoding="utf-8")
        body = "\n".join(
            line for line in src.splitlines()
            if not line.lstrip().startswith(("#", "*", '"""'))
        )
        self.assertNotRegex(
            body, r"\b[2-9]\d{3}\b",
            "test_parity.py hardcodes a cap-like number; it should read "
            "CONTRACT['total_description_cap'] so the gate cannot drift.",
        )

    def test_parity_test_reads_the_contract_for_its_caps(self):
        src = (REPO / "python" / "tests" / "test_parity.py").read_text(
            encoding="utf-8")
        self.assertIn("total_description_cap", src)
        self.assertIn("per_tool_description_cap", src)


if __name__ == "__main__":
    unittest.main()