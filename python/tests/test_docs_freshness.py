"""Fail when hand-written documentation contradicts the generated contract.

Every threshold here is derived from `contract/tools.json`, so the *enforcement*
cannot drift. The prose beside it did, three times: a total cap that survived a
raise, and two different headroom figures in adjacent bullets that disagreed with
each other and with the contract. Nothing failed, because nothing checked.

So these assert the *rule* rather than the value - no cap-like figure in the
normative section, and an index that matches the file it indexes. Neither test
encodes a number, so neither can rot the way the prose did.

`TODO.md` is deliberately exempt: it is a dated evidence log, and recording what
a number *was* is its job.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "AGENTS.md"
CONTRACT = REPO / "contract" / "tools.json"

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
            "contract/tools.json, which is generated - the prose is what drifted "
            "three times already. Point at the contract instead."
            % (found or ""),
        )

    def test_the_section_points_at_the_generated_contract(self):
        self.assertIn("contract/tools.json", self.section)

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
        passes. A prior freshness check sets the precedent in this repo, and
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


class TestEvidenceLogIsIndexed(unittest.TestCase):
    """A long evidence log must carry a contents list, and it must match.

    This caught real drift: three sections were added without refreshing the
    index, and the index came to list itself because the filter compared the
    whole line `## Contents` against `Contents`.

    Below `NEEDS_INDEX_AT` sections an index is noise, and its absence is
    correct - asserting one there would be a rule about layout rather than about
    being able to find things.
    """

    NEEDS_INDEX_AT = 6

    def setUp(self):
        self.lines = (REPO / "TODO.md").read_text(encoding="utf-8").splitlines()
        self.sections = [l[3:].strip() for l in self.lines
                         if l.startswith("## ") and not l.startswith("### ")
                         and l[3:].strip() != "Contents"]
        self.has_index = "## Contents" in self.lines

    def test_an_index_is_present_when_the_file_needs_one(self):
        if len(self.sections) >= self.NEEDS_INDEX_AT:
            self.assertTrue(
                self.has_index,
                f"TODO.md has {len(self.sections)} sections and no contents list. "
                "Anyone opening it has to read it to find out whether there is "
                "work to do.",
            )

    def test_when_present_the_index_matches_the_file(self):
        if not self.has_index:
            self.skipTest(f"only {len(self.sections)} sections; no index needed")
        start = self.lines.index("## Contents")
        end = next((i for i in range(start + 1, len(self.lines))
                    if self.lines[i].startswith("> ##")), len(self.lines))
        entries = [l for l in self.lines[start:end] if l.startswith("- [`")]
        named = [re.match(r"- \[`(.+?)`\]", e).group(1) for e in entries]
        self.assertEqual(
            len(entries), len(self.sections),
            f"{len(entries)} index entries vs {len(self.sections)} sections - "
            "regenerate the contents list",
        )
        self.assertNotIn("Contents", named, "the index lists itself")
        self.assertEqual(sorted(named), sorted(self.sections))


class TestEvidenceLogHasNotEatenItself(unittest.TestCase):
    """Stop the log growing by default placement rather than by decision.

    The failure this exists for is one commit old. `TODO.md` held a 1,290-line
    dated review for days, and in the session that removed it, five more sections
    were added *and* a banner was written warning that the file was too big to
    read. Documenting a known problem is not addressing it.

    **An earlier version of this gate used a *share* ceiling - no section over
    15% of the file - and was wrong.** It was read off the one failure above and
    then applied as a general rule, so it rejected the correct structure: in a
    file that is a backlog, the backlog legitimately *is* most of the file. The
    over-generalisation this repo keeps making, caught here by the gate failing
    on a file it should have passed.

    So the rule is **absolute**, which is what the failure actually was: no single
    section may exceed `MAX_SECTION_LINES`, however big the file is. A 1,290-line
    narrative section fails it; a 142-line open-items list does not.

    Two ceilings, because the two ways this fails differ:
      - **one section**, absolute, so narrative cannot swallow a backlog
      - **total bytes**, the context budget, which is the constraint that matters
    """

    MAX_SECTION_LINES = 400
    MAX_TOTAL_BYTES = 120 * 1024

    def setUp(self):
        self.path = REPO / "TODO.md"
        self.text = self.path.read_text(encoding="utf-8")
        self.lines = self.text.splitlines()

    def _sections(self):
        heads = [i for i, l in enumerate(self.lines)
                 if l.startswith("## ") and not l.startswith("### ")]
        out = []
        for k, start in enumerate(heads):
            end = heads[k + 1] if k + 1 < len(heads) else len(self.lines)
            out.append((self.lines[start][3:].strip(), end - start))
        return out

    def test_no_section_is_long_enough_to_be_a_dated_report(self):
        offenders = [(n, c) for n, c in self._sections() if not self._len_ok(c)]
        self.assertEqual(
            offenders, [],
            "section(s) over {} lines: {}. A section that long is a dated report "
            "wearing a section heading - that is the shape that grew this file to "
            "242 KB unnoticed. **Move it to docs/EVIDENCE.md and keep its open "
            "items**, which are recorded nowhere else."
            .format(self.MAX_SECTION_LINES,
                    ", ".join(f"{n!r} ({c} lines)" for n, c in offenders)),
        )

    def test_total_size_is_under_the_context_budget(self):
        size = self.path.stat().st_size
        self.assertTrue(
            self._size_ok(size),
            f"TODO.md is {size // 1024} KB, over the "
            f"{self.MAX_TOTAL_BYTES // 1024} KB ceiling. Roughly 60k tokens is "
            "enough to displace the work in context. Move closed research to "
            "docs/EVIDENCE.md; only open work and withdrawals belong here.",
        )

    def test_the_ceilings_can_actually_fail(self):
        """Negative control.

        A gate that cannot fail is indistinguishable from one that passes, which
        is the mistake this file was written about. An earlier draft asserted
        ``MAX * 2 > MAX`` for the byte ceiling - trivially true, proving nothing
        - and crashed on a list/int mix for the other. Both now breach the *real*
        extracted predicate and assert it rejects the breach, each with a passing
        counterpart so neither can be simply always-false.
        """
        self.assertFalse(self._len_ok(self.MAX_SECTION_LINES + 1),
                         "the section ceiling accepted a section over its limit")
        self.assertTrue(self._len_ok(self.MAX_SECTION_LINES - 1),
                        "the section ceiling rejected a section under its limit")

        self.assertFalse(self._size_ok(self.MAX_TOTAL_BYTES + 1),
                         "the byte ceiling accepted a file over its limit")
        self.assertTrue(self._size_ok(self.MAX_TOTAL_BYTES - 1),
                        "the byte ceiling rejected a file under its limit")

        # And the real file satisfies both, so the gate discriminates.
        self.assertTrue(self._size_ok(self.path.stat().st_size))
        self.assertTrue(all(self._len_ok(c) for _, c in self._sections()))

    def _len_ok(self, count: int) -> bool:
        return count <= self.MAX_SECTION_LINES

    def _size_ok(self, size: int) -> bool:
        return size <= self.MAX_TOTAL_BYTES


class TestContractTestReadsItsOwnCap(unittest.TestCase):

    def test_contract_test_does_not_hardcode_a_cap(self):
        src = (REPO / "python" / "tests" / "test_contract.py").read_text(
            encoding="utf-8")
        body = "\n".join(
            line for line in src.splitlines()
            if not line.lstrip().startswith(("#", "*", '"""'))
        )
        self.assertNotRegex(
            body, r"\b[2-9]\d{3}\b",
            "test_contract.py hardcodes a cap-like number; it should read "
            "CONTRACT['total_description_cap'] so the gate cannot drift.",
        )

    def test_contract_test_reads_the_contract_for_its_caps(self):
        src = (REPO / "python" / "tests" / "test_contract.py").read_text(
            encoding="utf-8")
        self.assertIn("total_description_cap", src)
        self.assertIn("per_tool_description_cap", src)


if __name__ == "__main__":
    unittest.main()