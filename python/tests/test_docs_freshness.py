"""Fail when hand-written documentation contradicts the generated contract.

Every threshold here is derived from `parity/tools.json`, so the *enforcement*
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


class TestEvidenceLogIsIndexed(unittest.TestCase):
    """`TODO.md` has a generated contents list, and it must match the file.

    It went stale the moment a section was added, which is the drift the index
    was built to stop. Three sections were missing for one commit.
    """

    def setUp(self):
        self.lines = (REPO / "TODO.md").read_text(encoding="utf-8").splitlines()
        start = self.lines.index("## Contents")
        end = next(i for i in range(start + 1, len(self.lines))
                   if self.lines[i].startswith("> ##"))
        self.entries = [l for l in self.lines[start:end] if l.startswith("- [`")]
        # Compare the extracted title, not the line. Getting this wrong is how
        # the index came to list itself: the filter read `## Contents` and
        # compared it to `Contents`.
        self.sections = [l[3:].strip() for l in self.lines
                         if l.startswith("## ") and not l.startswith("### ")
                         and l[3:].strip() != "Contents"]

    def test_one_entry_per_section(self):
        self.assertEqual(len(self.entries), len(self.sections),
                         f"{len(self.entries)} entries vs {len(self.sections)} "
                         "sections - regenerate the contents list")

    def test_the_index_does_not_list_itself(self):
        self.assertNotIn("Contents",
                         [re.match(r"- \[`(.+?)`\]", e).group(1) for e in self.entries])

    def test_every_entry_names_a_real_section(self):
        named = [re.match(r"- \[`(.+?)`\]", e).group(1) for e in self.entries]
        self.assertEqual(set(named), set(self.sections))


class TestEvidenceLogHasNotEatenItself(unittest.TestCase):
    """Stop the log growing by default placement rather than by decision.

    The failure this exists for is one commit old. `TODO.md` held a 1,290-line
    dated review - **31% of the file** - for days, and in the session that finally
    removed it, five more sections were added *and* a banner was written warning
    that the file was too big to read. Documenting a known problem is not
    addressing it, and this repo already has a section cataloguing that failure
    class in a different costume.

    Two ceilings, because the two ways it fails are different:

    - **One section dominating.** Share-based, so it scales with the file and
      fires on *concentration* rather than on size. A log with fifty short
      sections is large and fine; a log where one dated report is a third of it is
      neither.
    - **Total size.** The backstop for growth spread thinly across many sections,
      which the share check would not see.

    Both report **extract, do not delete**: every section cut today held open
    items recorded nowhere else.
    """

    MAX_SECTION_SHARE = 0.15
    MAX_TOTAL_BYTES = 250 * 1024

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

    def test_no_single_section_dominates_the_file(self):
        total = len(self.lines)
        ceiling = total * self.MAX_SECTION_SHARE
        offenders = [(n, c) for n, c in self._sections() if not self._share_ok(
            [(n, c)], total)]
        self.assertEqual(
            offenders, [],
            "section(s) over {:.0%} of TODO.md: {}. A dated section that swallows "
            "the log is the shape that grew it to 242 KB unnoticed. **Extract its "
            "open items into their own section, then delete the narrative** - do "
            "not delete the items, which are recorded nowhere else."
            .format(self.MAX_SECTION_SHARE,
                    ", ".join(f"{n!r} ({c} lines)" for n, c in offenders)),
        )

    def test_total_size_is_under_the_ceiling(self):
        size = self.path.stat().st_size
        self.assertTrue(
            self._size_ok(size),
            f"TODO.md is {size // 1024} KB, over the {self.MAX_TOTAL_BYTES // 1024} KB "
            f"TODO.md is {size // 1024} KB, over the {self.MAX_TOTAL_BYTES // 1024} KB "
            "ceiling. Roughly 60k tokens is enough to displace the work in "
            "context. Split by subject, or move a dated report out and carry its "
            "open items forward.",
        )

    def test_the_ceilings_can_actually_fail(self):
        """Negative control.

        A gate that cannot fail is indistinguishable from one that passes, which
        is the mistake this file was written about. An earlier draft of this
        control asserted ``MAX * 2 > MAX`` for the byte ceiling - trivially true,
        proving nothing - and crashed on a list/int mix for the share ceiling.
        Both now breach the *real* predicate and assert it rejects the breach.

        The predicates are extracted rather than restated so the control cannot
        pass against a copy of the logic rather than the logic itself.
        """
        share_ok = self._share_ok
        size_ok = self._size_ok

        # Share: a section deliberately over the ceiling.
        ceiling = len(self.lines) * self.MAX_SECTION_SHARE
        bloated = ["## A dated report nobody split", *["filler"] * (int(ceiling) + 50)]
        self.assertFalse(
            share_ok([("A dated report nobody split", len(bloated) - 1)], len(bloated)),
            "the share ceiling accepted a section over its limit",
        )

        # And it accepts the real thing, so it is discriminating rather than
        # simply always-false.
        self.assertTrue(
            share_ok(self._sections(), len(self.lines)),
            "the share ceiling rejected the current file",
        )

        # Bytes: a payload over the limit, and one under it.
        self.assertFalse(
            size_ok(self.MAX_TOTAL_BYTES + 1),
            "the byte ceiling accepted a file over its limit",
        )
        self.assertTrue(size_ok(self.MAX_TOTAL_BYTES - 1),
                        "the byte ceiling rejected a file under its limit")

    def _share_ok(self, sections, total) -> bool:
        ceiling = total * self.MAX_SECTION_SHARE
        return all(count <= ceiling for _, count in sections)

    def _size_ok(self, size: int) -> bool:
        return size <= self.MAX_TOTAL_BYTES


class TestParityTestReadsItsOwnCap(unittest.TestCase):

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