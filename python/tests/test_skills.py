"""Tests for the skills loader.

The skills are shared source, so the important properties are that both
implementations read the same files, that a malformed file is reported rather
than silently skipped, and that the index stays far smaller than the bodies it
indexes.
"""

import os
import tempfile
import unittest

from roblox_studio_mcp.extended.skills import (
    SKILL_IGNORED,
    SkillError,
    call_skill,
    find_skills_dir,
    get_skill,
    load_skills,
    skill_index,
)

SKILL = """---
name: {name}
description: {description}
---

# {name}

body text here
"""


class TestRealSkills(unittest.IsolatedAsyncioTestCase):
    def test_skills_folder_is_found(self):
        self.assertIsNotNone(find_skills_dir(), "no skills/ folder found above the package")

    def test_real_skills_load(self):
        skills = load_skills()
        self.assertGreaterEqual(len(skills), 5, "expected the shipped skills to be present")
        names = [s["name"] for s in skills]
        self.assertEqual(names, sorted(names), "index order must be stable")
        for skill in skills:
            self.assertTrue(skill["name"].startswith("rsx-"), skill["name"])
            self.assertTrue(skill["content"].strip(), f"{skill['name']} has an empty body")

    def test_index_is_much_smaller_than_the_bodies(self):
        # The whole point of the design: the index is paid on every call, the
        # bodies only on demand. If this inverts, the split has stopped working.
        skills = load_skills()
        index_chars = len(skill_index(skills))
        body_chars = sum(len(s["content"]) for s in skills)
        self.assertLess(index_chars, body_chars // 4)

    def test_descriptions_are_one_line_and_short(self):
        for skill in load_skills():
            desc = skill["description"]
            self.assertLessEqual(len(desc), 160, f"{skill['name']} description is too long")
            self.assertNotIn("\n", desc)

    def test_fetching_a_skill_drops_the_frontmatter(self):
        text, meta = call_skill("rsx-transport")
        self.assertTrue(text.startswith("# rsx-transport"))
        self.assertNotIn("description:", text.split("\n\n")[0])
        self.assertEqual(meta["name"], "rsx-transport")
        self.assertGreater(meta["chars"], 500, "a real skill should have real content")

    def test_unknown_name_suggests_a_near_miss(self):
        with self.assertRaises(SkillError) as caught:
            get_skill("rsx-transprot")
        self.assertIn("rsx-transport", str(caught.exception))


class TestLoaderEdgeCases(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_missing_frontmatter_is_reported(self):
        self.write("rsx-bad.md", "# no frontmatter here\n")
        with self.assertRaises(SkillError) as caught:
            load_skills(self.dir)
        self.assertIn("frontmatter", str(caught.exception))

    def test_name_mismatch_is_reported(self):
        self.write("rsx-wrong.md", SKILL.format(name="rsx-other", description="d"))
        with self.assertRaises(SkillError) as caught:
            load_skills(self.dir)
        self.assertIn("does not match", str(caught.exception))

    def test_missing_description_is_reported(self):
        self.write("rsx-nodesc.md", "---\nname: rsx-nodesc\n---\n\nbody\n")
        with self.assertRaises(SkillError) as caught:
            load_skills(self.dir)
        self.assertIn("description", str(caught.exception))

    def test_readme_is_not_treated_as_a_skill(self):
        self.write("README.md", "# readme\n")
        self.write("rsx-ok.md", SKILL.format(name="rsx-ok", description="d"))
        self.assertEqual([s["name"] for s in load_skills(self.dir)], ["rsx-ok"])
        self.assertIn("README.md", SKILL_IGNORED)

    def test_empty_directory_reports_clearly(self):
        index = skill_index(load_skills(self.dir))
        self.assertIn("No skills found", index)

    def test_crlf_frontmatter_is_accepted(self):
        self.write("rsx-crlf.md", "---\r\nname: rsx-crlf\r\ndescription: d\r\n---\r\n\r\nbody\r\n")
        skills = load_skills(self.dir)
        self.assertEqual(skills[0]["name"], "rsx-crlf")
        self.assertIn("body", skills[0]["content"])

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(load_skills(os.path.join(self.dir, "nope")), [])


if __name__ == "__main__":
    unittest.main()
