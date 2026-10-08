"""Tests for the skills loader.

The skills ship inside the package (``roblox_studio_mcp/skills``, via
``[tool.setuptools.package-data]``), so the properties worth pinning are that an
*install* resolves them beside itself, that a missing or empty folder is an error
rather than an empty catalogue, that a malformed file is reported rather than
silently skipped, and that the index stays far smaller than the bodies it
indexes.

``TestPackaging`` is the wall this bug hit: the wheel built without the
``package-data`` glob installed the modules and none of the skills, the loader
returned ``[]``, and the tool answered successfully with an empty catalogue.
Nothing failed, because nothing looked at the wheel.
"""

import asyncio
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import List, Optional
from unittest import mock

from roblox_studio_mcp import __file__ as _PACKAGE_FILE
from roblox_studio_mcp import extended_server as es
from roblox_studio_mcp.extended import skills as skills_module
from roblox_studio_mcp.extended.skills import (
    SKILL_IGNORED,
    SkillError,
    call_skill,
    find_skills_dir,
    get_skill,
    load_skills,
    skill_index,
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PYTHON = os.path.join(_REPO, "python")
_PYPROJECT = os.path.join(_PYTHON, "pyproject.toml")
_PACKAGE_ROOT = os.path.dirname(_PACKAGE_FILE)
_PACKAGED_SKILLS = os.path.join(_PACKAGE_ROOT, "skills")

SKILL = """---
name: {name}
description: {description}
---

# {name}

body text here
"""


def _source_skill_files() -> List[str]:
    """Every markdown file sitting in the package's own skills folder."""
    return sorted(n for n in os.listdir(_PACKAGED_SKILLS) if n.endswith(".md"))


def _expected_skill_names() -> List[str]:
    """The names a correct load of the shipped folder produces."""
    return sorted(n[: -len(".md")] for n in _source_skill_files() if n not in SKILL_IGNORED)


def _package_data_globs() -> Optional[List[str]]:
    """The ``[tool.setuptools.package-data]`` globs for the model, read as text.

    ``tomllib`` is 3.11+ and this project's floor is 3.9, so importing it would
    skip exactly the two oldest supported versions - and a packaging test that
    skips where the packaging is least exercised is worse than none. The regex is
    narrow on purpose: it finds this model's own array and nothing else.
    """
    text = open(_PYPROJECT, "r", encoding="utf-8").read()
    parts = text.split("[tool.setuptools.package-data]", 1)
    if len(parts) != 2:
        return None
    match = re.search(
        r"^\s*roblox_studio_mcp\s*=\s*\[(?P<items>[^\]]*)\]", parts[1], re.MULTILINE
    )
    if match is None:
        return []
    return [i.strip().strip("\"'") for i in match.group("items").split(",") if i.strip()]


class TestRealSkills(unittest.IsolatedAsyncioTestCase):
    def test_skills_folder_is_found(self):
        self.assertIsNotNone(find_skills_dir(), "no skills/ folder found")

    def test_the_folder_found_is_the_packaged_one(self):
        """Resolution must not depend on where the process was started from.

        This is the regression test for the silent failure. With the skills back
        at the repository root, ``find_skills_dir()`` still answers - by walking
        up out of the package - so a test that only asserts "not None" passes
        while every installed copy loads nothing.
        """
        found = find_skills_dir()
        self.assertEqual(
            os.path.normpath(found or ""),
            os.path.normpath(_PACKAGED_SKILLS),
            "the skills must resolve beside the package, not by walking up",
        )

    def test_real_skills_load(self):
        skills = load_skills()
        self.assertEqual(
            [s["name"] for s in skills],
            _expected_skill_names(),
            "the shipped folder and the loaded catalogue disagree",
        )
        for skill in skills:
            self.assertTrue(skill["name"].startswith("rsx-"), skill["name"])
            self.assertTrue(skill["content"].strip(), f"{skill['name']} has an empty body")
            self.assertTrue(
                os.path.isabs(skill["path"]) and os.path.isfile(skill["path"]),
                f"{skill['name']} path does not resolve: {skill['path']}",
            )

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

    def test_crlf_frontmatter_is_accepted(self):
        self.write("rsx-crlf.md", "---\r\nname: rsx-crlf\r\ndescription: d\r\n---\r\n\r\nbody\r\n")
        skills = load_skills(self.dir)
        self.assertEqual(skills[0]["name"], "rsx-crlf")
        self.assertIn("body", skills[0]["content"])

    def test_an_explicit_start_still_walks_up(self):
        # `start` is the caller's own folder. The package-relative lookup is for
        # the no-argument call only, and this pins that it did not replace it.
        # The skills folder sits two levels above `start`, so the walk has real
        # work to do and could not answer by looking next to itself.
        nested = os.path.join(self.dir, "nested")
        os.makedirs(os.path.join(nested, "a", "b"))
        os.makedirs(os.path.join(nested, "skills"))
        with open(
            os.path.join(nested, "skills", "rsx-ok.md"), "w", encoding="utf-8"
        ) as handle:
            handle.write(SKILL.format(name="rsx-ok", description="d"))
        found = find_skills_dir(os.path.join(nested, "a", "b"))
        self.assertEqual(
            os.path.normpath(found or ""),
            os.path.normpath(os.path.join(nested, "skills")),
            "an explicit start must still be walked up from",
        )

    def test_empty_directory_raises_rather_than_returning_nothing(self):
        # The failure class this project exists to prevent: `[]` reads as "there
        # are no skills", which is a working answer for a broken install.
        with self.assertRaises(SkillError) as caught:
            load_skills(self.dir)
        self.assertIn(self.dir, str(caught.exception))

    def test_missing_directory_raises_naming_the_path(self):
        missing = os.path.join(self.dir, "nope")
        with self.assertRaises(SkillError) as caught:
            load_skills(missing)
        self.assertIn("nope", str(caught.exception))

    def test_the_defensive_index_arm_is_only_reachable_by_passing_nothing(self):
        # `load_skills()` cannot return [], so nothing reachable from it can
        # render the empty index. Passing an empty list in still gets prose that
        # says so, rather than an empty <available_skills> block.
        index = skill_index([])
        self.assertIn("No skills given to render", index)
        self.assertNotIn("<available_skills>", index)


class TestTheLoudFailure(unittest.IsolatedAsyncioTestCase):
    """A missing skills folder has to reach the caller as a failure.

    The bug was not only that the wheel shipped no skills: it was that the tool
    answered ``skill names: []`` with ``isError: false``. So these drive the real
    dispatch path with the folder taken away entirely.
    """

    def _dispatch_index(self):
        sent = []
        with mock.patch.object(es, "_send", side_effect=lambda m: sent.append(m)):
            asyncio.new_event_loop().run_until_complete(
                asyncio.wait_for(
                    es._handle_message(
                        None,
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {"name": "extended_skill", "arguments": {}},
                        },
                    ),
                    timeout=20.0,
                )
            )
        self.assertEqual(len(sent), 1, "one response, not several")
        return sent[0]

    def test_a_missing_folder_is_an_error_not_an_empty_catalogue(self):
        with mock.patch.object(skills_module, "find_skills_dir", return_value=None):
            response = self._dispatch_index()
        self.assertIn("error", response, f"answered successfully with {response}")
        code = response["error"].get("data", {}).get("code")
        self.assertIsNotNone(code, f"no stable code in {response}")

    def test_the_error_names_what_was_looked_for(self):
        with mock.patch.object(skills_module, "find_skills_dir", return_value=None):
            with self.assertRaises(SkillError) as caught:
                load_skills()
        message = str(caught.exception)
        self.assertIn("skills", message)
        self.assertIn(os.path.normpath(_PACKAGED_SKILLS), os.path.normpath(message))


class TestPackaging(unittest.TestCase):
    """The skills have to be *in the wheel*, not merely findable in a checkout.

    Two halves, because the bug had two halves. The wheel is really built - from
    a copy, so the tree is left alone - and its listing read, which is the only
    way to see what an install actually receives. The install layout is then
    simulated: the package alone, imported from outside the repository, with no
    working directory that could rescue it.
    """

    def test_pyproject_declares_the_skills_as_package_data(self):
        globs = _package_data_globs()
        self.assertIsNotNone(globs, "no [tool.setuptools.package-data] table")
        self.assertTrue(
            any(fnmatch.fnmatch("skills/rsx-x.md", g) for g in globs or []),
            f"no glob ships the skills folder: {globs}",
        )

    def test_every_skill_file_matches_a_declared_glob(self):
        # A new skill file is only shipped if a glob matches it. Adding one and
        # forgetting the glob is exactly the shape of this bug, so coverage is
        # asserted rather than assumed.
        globs = _package_data_globs() or []
        shipped = _source_skill_files()
        self.assertTrue(shipped, f"{_PACKAGED_SKILLS} holds no markdown at all")
        for name in shipped:
            self.assertTrue(
                any(fnmatch.fnmatch(f"skills/{name}", g) for g in globs),
                f"skills/{name} is not covered by any package-data glob: {globs}",
            )

    def test_the_wheel_contains_every_skill(self):
        listing = self._wheel_listing()
        if listing is None:
            self.skipTest("setuptools is not installed, so no wheel can be built here")
        md = sorted(n for n in listing if n.endswith(".md"))
        expected = sorted(f"roblox_studio_mcp/skills/{n}" for n in _source_skill_files())
        self.assertEqual(md, expected, "the wheel's markdown differs from the source folder")
        # The data has to sit *inside* the package: that prefix is what makes
        # find_skills_dir's package-relative lookup work in an install.
        self.assertTrue(all(n.startswith("roblox_studio_mcp/skills/") for n in md))

    def test_an_install_outside_the_repo_loads_every_skill(self):
        """Copy the package alone somewhere else and import it from there.

        A source tree is full of ways for a walk-up to succeed by accident. An
        install has none, so the subprocess runs with the repository out of
        ``PYTHONPATH``, outside the repository as CWD, and only the package copy
        on the path - which is what a wheel unpacked into site-packages is.
        """
        expected = _expected_skill_names()
        with tempfile.TemporaryDirectory() as tmp:
            site = os.path.join(tmp, "site-packages")
            os.makedirs(site)
            shutil.copytree(
                _PACKAGE_ROOT,
                os.path.join(site, "roblox_studio_mcp"),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env["PYTHONPATH"] = site
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json\n"
                    "import roblox_studio_mcp\n"
                    "from roblox_studio_mcp.extended.skills import (\n"
                    "    find_skills_dir, load_skills)\n"
                    "loaded = load_skills()\n"
                    "print(json.dumps({\n"
                    "    'dir': find_skills_dir(),\n"
                    "    'names': [s['name'] for s in loaded],\n"
                    "    'package': roblox_studio_mcp.__file__,\n"
                    "}))",
                ],
                env=env,
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(
            proc.returncode,
            0,
            "the installed package failed to load its skills:\n"
            + (proc.stdout or "")[-2000:]
            + "\n"
            + (proc.stderr or "")[-2000:],
        )
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["names"], expected, f"loaded {payload['names']}")
        self.assertEqual(
            os.path.normpath(payload["dir"]),
            os.path.normpath(os.path.join(os.path.dirname(payload["package"]), "skills")),
            "an install must resolve the skills beside the package",
        )

    def _wheel_listing(self) -> Optional[List[str]]:
        """Build a real wheel from a copy of the tree and return its listing.

        In-process would write ``build/`` and ``*.egg-info`` into the tree, so
        the build runs in a subprocess against a copy. The backend is the one
        ``pyproject.toml`` already requires, so this needs no network and no
        isolated environment.
        """
        if importlib.util.find_spec("setuptools") is None:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "build")
            os.makedirs(os.path.join(work, "src"))
            shutil.copy2(_PYPROJECT, work)
            shutil.copy2(os.path.join(_PYTHON, "README.md"), work)
            shutil.copytree(
                _PACKAGE_ROOT,
                os.path.join(work, "src", "roblox_studio_mcp"),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            script = (
                "import json, os, tempfile, zipfile\n"
                "import setuptools.build_meta as backend\n"
                "out = tempfile.mkdtemp()\n"
                "wheel = backend.build_wheel(out)\n"
                "names = sorted(zipfile.ZipFile(os.path.join(out, wheel)).namelist())\n"
                "print(json.dumps(names))\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=300,
                env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            )
        if proc.returncode != 0:
            self.fail(
                "wheel build failed:\n"
                + (proc.stdout or "")[-3000:]
                + "\n"
                + (proc.stderr or "")[-3000:]
            )
        return json.loads(proc.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
