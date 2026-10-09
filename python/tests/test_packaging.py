"""The PyPI record, checked in the artifact that actually gets uploaded.

Every assertion below reads the *built wheel* - `dist-info/METADATA`, the
`dist-info/licenses/` directory and `dist-info/entry_points.txt` - not
`pyproject.toml`.
The TOML says what is intended; PyPI renders what setuptools emitted from it,
and the two are not the same thing.

**Why this file exists.** The wheel was built and `dist-info/METADATA` read end
to end on 2026-10-08, and seven gaps were visible in the output that were
invisible in the source: no author, no project URLs, no license file in the
wheel at all, no console script, a deprecated license table that warned on every
build, a `requires-python` floor that no CI job exercised, and a landing page
with no AI-authorship disclosure. Nothing failed, because nothing looked.

**Three of these failure modes are silent, which is what makes the checks
specific:**

* A `license-files` glob that matches nothing **exits 0** with a
  `SetuptoolsDeprecationWarning` on stderr and simply ships no license. So the
  build must be checked for the warning, not just for the file it was expected
  to add.
* `license-files = ["../LICENSE"]` *does* put the file in the wheel - and warns
  `Pattern '../LICENSE' cannot contain '..'`, which becomes a hard failure on the
  same 2027-Feb-18 date as the deprecated table. Both forms are refused here.
* A version declared in two places drifts without either file changing again.

The floor decision is also pinned here: every `Programming Language :: Python`
classifier must name a version that `.github/workflows/ci.yml` actually runs. A
classifier is a claim about four interpreters; the matrix is what makes it true.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PYTHON = os.path.join(_REPO, "python")
_PYPROJECT = os.path.join(_PYTHON, "pyproject.toml")
_PACKAGE = os.path.join(_PYTHON, "src", "roblox_studio_mcp")
_ROOT_LICENSE = os.path.join(_REPO, "LICENSE")
_PACKAGE_LICENSE = os.path.join(_PYTHON, "LICENSE")
_WORKFLOW = os.path.join(_REPO, ".github", "workflows", "ci.yml")
_REPO_URL = "https://github.com/bdidk235/RobloxStudioMCP"

# setuptools prints all three of these to stderr and still exits 0. The first
# two are the license failures above; the third is the deprecated table form.
_BUILD_WARNING_MARKERS = (
    "did not match any files",
    "cannot contain '..'",
    "TOML table is deprecated",
)


def _pyproject_text() -> str:
    with open(_PYPROJECT, encoding="utf-8") as handle:
        return handle.read()


def _section(name: str) -> str:
    """The body of one pyproject table, without its sub-tables or comments.

    `[project.urls]` is its own header in TOML, so `_section("project")` stops
    before it - which is what keeps a `Homepage = ...` line from being read as a
    `[project]` key.

    Full-line comments are dropped, and only full-line ones: `Documentation`
    legitimately ends in `#readme`, so a blanket `#` strip would truncate a URL.
    Stripping comments is not cosmetic either - a comment quoting the deprecated
    `license = { text = ... }` form tripped the very guard that forbids it.
    """
    text = _pyproject_text()
    header = re.search(rf"(?m)^\[{re.escape(name)}\]\s*$", text)
    if header is None:
        return ""
    body = text[header.end():]
    following = re.search(r"(?m)^\[", body)
    return re.sub(r"(?m)^\s*#[^\n]*\n?", "", body[: following.start()] if following else body)


def _project_value(key: str) -> str:
    """A scalar `key = value` from `[project]`, unquoted, or ""."""
    match = re.search(rf'(?m)^{key}\s*=\s*"([^"]*)"', _section("project"))
    return match.group(1) if match else ""


def _declared_version_in(text: str) -> str:
    """`version = "..."` in an arbitrary pyproject body, so the same reader can
    be pointed at a mutated copy for the negative control below."""
    match = re.search(r'(?m)^version\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else ""


def _classifiers() -> list:
    return re.findall(r'(?m)^\s*"([^"]+)",', _section("project") + "\n")


def _build_wheel() -> dict:
    """Build a real wheel from a copy of the tree and return everything readable.

    In-process would write `build/` and `*.egg-info` into the source tree, so the
    build runs in a subprocess against a copy. The backend is the one
    `pyproject.toml` already requires, so this needs no network and no isolated
    environment.

    The copy must contain **every file the build reads** - `pyproject.toml`, the
    `readme` it names, and the `license-files` it names. Omit one and setuptools
    warns and ships without it, which is the whole silent failure this file is
    about, so the file list is here rather than in a helper three tests away.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "build")
        os.makedirs(os.path.join(work, "src"))
        shutil.copy2(_PYPROJECT, work)
        readme = _project_value("readme")
        self_readme = os.path.join(_PYTHON, readme)
        shutil.copy2(self_readme, work)
        for name in re.findall(r'"([^"]+)"', _project_array("license-files")):
            shutil.copy2(os.path.join(_PYTHON, name), work)
        shutil.copytree(
            _PACKAGE,
            os.path.join(work, "src", "roblox_studio_mcp"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        script = (
            "import json, os, tempfile, zipfile\n"
            "import setuptools.build_meta as backend\n"
            "out = tempfile.mkdtemp()\n"
            "wheel = backend.build_wheel(out)\n"
            "archive = zipfile.ZipFile(os.path.join(out, wheel))\n"
            "names = sorted(archive.namelist())\n"
            "metadata = [n for n in names if n.endswith('.dist-info/METADATA')][0]\n"
            "entry = [n for n in names if n.endswith('.dist-info/entry_points.txt')]\n"
            "print(json.dumps({\n"
            "    'names': names,\n"
            "    'metadata': archive.read(metadata).decode('utf-8'),\n"
            "    'entry_points': archive.read(entry[0]).decode('utf-8') if entry else '',\n"
            "}))\n"
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
        raise AssertionError(
            "wheel build failed:\n"
            + (proc.stdout or "")[-2000:]
            + "\n"
            + (proc.stderr or "")[-2000:]
        )
    return {
        "payload": json.loads(proc.stdout.strip().splitlines()[-1]),
        "stderr": proc.stderr or "",
    }


def _project_array(key: str) -> str:
    """The raw text of an array-valued `[project]` key, or ""."""
    body = _section("project")
    match = re.search(rf"(?ms)^{key}\s*=\s*\[(.*?)\]", body)
    return match.group(1) if match else ""


class TestTheDeclarationIsWhatItClaims(unittest.TestCase):
    """The TOML shape, read without a TOML parser.

    `tomllib` is 3.11+ and the floor is 3.9, and `tomli` would be a dependency
    bought for four checks. The two things that have to be read are flat
    one-line scalars and two small tables, so targeted readers keep the floor
    dependency-free.
    """

    def test_license_is_an_spdx_string_not_the_deprecated_table(self):
        body = _section("project")
        self.assertRegex(body, r'(?m)^license\s*=\s*"MIT"')
        self.assertNotIn("license = {", body)
        self.assertNotIn("text =", body)

    def test_license_files_are_inside_the_project_not_above_it(self):
        patterns = re.findall(r'"([^"]+)"', _project_array("license-files"))
        self.assertTrue(patterns, "no license-files are declared")
        for pattern in patterns:
            self.assertNotIn(
                "..",
                os.path.normpath(pattern).split(os.sep),
                "'%s' escapes python/ and setuptools refuses it as of 2027-02-18"
                % pattern,
            )

    def test_the_license_shipped_with_the_package_is_the_root_one(self):
        """The copy is pinned to the repository's LICENSE, byte for byte.

        `license-files` is globbed relative to `pyproject.toml`, which is in
        `python/`, while the repository's LICENSE is one level up. So the text is
        carried here too, and a divergence between the two is a test failure
        rather than a release-day surprise.
        """
        with open(_PACKAGE_LICENSE, "rb") as handle:
            packaged = handle.read()
        with open(_ROOT_LICENSE, "rb") as handle:
            root = handle.read()
        self.assertEqual(
            packaged,
            root,
            "python/LICENSE has drifted from the repository's LICENSE; the two "
            "must be identical, because only the one in python/ is shipped",
        )

    def test_an_author_is_declared(self):
        authors = _project_array("authors")
        self.assertTrue(authors.strip(), "no authors are declared")
        self.assertRegex(authors, r'name\s*=\s*"[^"]+"')
        self.assertRegex(authors, r'email\s*=\s*"[^"]+@[^"]+"')

    def test_every_project_url_is_declared_and_points_at_the_repo(self):
        urls = re.findall(r'(?m)^([A-Za-z][\w-]*)\s*=\s*"([^"]+)"', _section("project.urls"))
        found = dict(urls)
        for label in ("Homepage", "Repository", "Issues", "Documentation"):
            self.assertIn(label, f"missing Project-URL {label}: {found}")
        for label, url in urls:
            self.assertTrue(url.startswith("https://"), f"{label} is not https: {url}")
            self.assertTrue(
                url.startswith(_REPO_URL),
                f"{label} points outside the repository: {url}",
            )

    def test_the_console_script_targets_a_callable(self):
        scripts = dict(
            re.findall(r'(?m)^([\w.-]+)\s*=\s*"([^"]+)"', _section("project.scripts"))
        )
        self.assertIn("roblox-studio-mcp", scripts)
        module_name, _, attribute = scripts["roblox-studio-mcp"].partition(":")
        self.assertEqual(attribute, "main")
        # The target must import *and* be callable, or the wheel installs a
        # launcher that fails on first run rather than at install time.
        module = __import__(module_name, fromlist=[attribute])
        self.assertTrue(callable(getattr(module, attribute)))

    def test_the_readme_carries_the_ai_disclosure_above_the_fold(self):
        """The wheel renders `readme` as the PyPI landing page, so that file is
        what a visitor reads. The repository's own trust anchor - the disclosure
        that this project is AI-written - has to be in it, and near the top: a
        disclosure below the PyPI fold is not disclosed."""
        readme = _project_value("readme")
        self.assertTrue(readme, "no readme is declared")
        path = os.path.join(_PYTHON, readme)
        with open(path, encoding="utf-8") as handle:
            head = "".join(handle.readlines()[:20])
        self.assertIn("AI-written", head)


class TestTheFloorIsExercised(unittest.TestCase):
    """`requires-python` and the classifiers are claims about interpreters.

    Every version named by a classifier must appear in the CI matrix, because
    the found state was four versions claimed and one tested - a claim nothing
    would have caught if nothing checked.
    """

    def test_every_classified_version_is_in_the_ci_matrix(self):
        declared = set()
        for classifier in _classifiers():
            match = re.match(r"Programming Language :: Python :: 3\.(\d+)$", classifier)
            if match:
                declared.add(match.group(1))
        self.assertTrue(declared, "no Python version is classified")
        with open(_WORKFLOW, encoding="utf-8") as handle:
            workflow = handle.read()
        matrix = re.search(r"(?ms)^\s*python-version:\s*\[(.*?)\]", workflow)
        self.assertIsNotNone(matrix, "ci.yml has no python-version matrix")
        exercised = set(re.findall(r'"3\.(\d+)"', matrix.group(1)))
        self.assertEqual(
            declared - exercised,
            set(),
            "classifier versions absent from the CI matrix: %s" % (declared - exercised),
        )

    def test_the_floor_is_what_the_static_gate_targets(self):
        """pyright's `pythonVersion` is the other place the floor is stated.

        Aligning it makes the type gate resolve `src/` against the floor's
        standard library, which is what turns the claim into something a check
        can see. It is partial - typeshed does not version-gate every symbol
        (`typing.is_typeddict` slipped through a negative control on 2026-10-08)
        - so it is a backstop for the CI matrix, not a replacement.
        """
        with open(os.path.join(_PYTHON, "pyrightconfig.json"), encoding="utf-8") as handle:
            config = json.load(handle)
        floor = _project_value("requires-python")
        self.assertTrue(floor, "no requires-python is declared")
        match = re.search(r">=\s*3\.(\d+)", floor)
        self.assertIsNotNone(match, floor)
        self.assertEqual(config.get("pythonVersion"), "3.%s" % match.group(1))

    def test_the_classifiers_have_no_gaps(self):
        minors = sorted(
            int(match.group(1))
            for c in _classifiers()
            for match in [re.match(r"Programming Language :: Python :: 3\.(\d+)$", c)]
            if match
        )
        self.assertTrue(minors, "no Python version is classified")
        self.assertEqual(
            list(range(minors[0], minors[-1] + 1)),
            minors,
            "the classified versions skip one, which reads as support that was "
            "dropped rather than never claimed",
        )


class TestTheVersionIsInOnePlace(unittest.TestCase):
    """`python/pyproject.toml` and `_version.py` both carry it.

    They matched on 2026-10-08 and nothing kept them matching. This is the thing
    that keeps them matching.
    """

    def test_the_declared_version_matches_the_package(self):
        from roblox_studio_mcp import __version__

        declared = _declared_version_in(_pyproject_text())
        self.assertTrue(declared, "pyproject.toml declares no version")
        self.assertEqual(
            declared,
            __version__,
            "pyproject.toml says %s and roblox_studio_mcp.__version__ says %s; "
            "they ship as one artifact, so one of them is wrong"
            % (declared, __version__),
        )

    def test_the_reader_would_actually_catch_a_drift(self):
        """Negative control. The failure mode is a parity test that compares the
        package against itself - which passes on any drift at all. So the reader
        is pointed at a text whose version is wrong and must return that one.

        The poison goes first, because the reader takes the first match: appended,
        this assertion would pass against the real pyproject and prove nothing.
        """
        from roblox_studio_mcp import __version__

        drifted = _declared_version_in('\nversion = "9.9.9"\n' + _pyproject_text())
        self.assertEqual(drifted, "9.9.9")
        self.assertNotEqual(
            drifted,
            __version__,
            "the reader returned the package's version instead of the one in the "
            "text, so it cannot detect drift",
        )


class TestTheBuiltWheelCarriesTheMetadata(unittest.TestCase):
    """Read `dist-info/METADATA` from a real build.

    This is the assertion that matters, because METADATA is the file PyPI
    renders and none of the checks above look at it.
    """

    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("setuptools") is None:
            raise unittest.SkipTest("setuptools is not installed, so no wheel can be built here")
        cls.built = _build_wheel()

    def _metadata(self) -> dict:
        fields = {}
        for line in self.built["payload"]["metadata"].splitlines():
            if not line or line.startswith(" "):
                break
            key, _, value = line.partition(": ")
            fields.setdefault(key, []).append(value)
        return fields

    def test_the_build_warned_about_nothing(self):
        """The silent half of the license bug.

        A `license-files` pattern that matches nothing exits 0, prints a
        `SetuptoolsDeprecationWarning` to stderr, and ships a wheel with no
        license in it. So the build's own stderr is the signal, and an empty
        wheel-level check is not evidence of a clean build.
        """
        found = [m for m in _BUILD_WARNING_MARKERS if m in self.built["stderr"]]
        self.assertEqual(
            found,
            [],
            "the build warned %s and still exited 0; it shipped without what the "
            "warning names" % found,
        )

    def test_the_author_is_in_the_metadata(self):
        author = self._metadata().get("Author-email", [""])[0]
        self.assertRegex(author, r".+ <[^@]+@[^>]+>", "METADATA carries no author: %r" % author)

    def test_the_license_is_an_spdx_expression(self):
        self.assertEqual(self._metadata().get("License-Expression"), ["MIT"])
        self.assertNotIn("License", self._metadata(), "the deprecated License field is still emitted")

    def test_the_project_urls_are_in_the_metadata(self):
        urls = dict(
            url.split(", ", 1)
            for url in self._metadata().get("Project-URL", [])
            if ", " in url
        )
        for label in ("Homepage", "Repository", "Issues", "Documentation"):
            self.assertIn(label, f"METADATA carries no Project-URL {label}: {urls}")

    def test_the_license_file_is_in_the_wheel(self):
        names = self.built["payload"]["names"]
        shipped = [n for n in names if n.endswith(".dist-info/licenses/LICENSE")]
        self.assertEqual(
            len(shipped),
            1,
            "the wheel carries no LICENSE: %s" % [n for n in names if "licenses" in n],
        )
        self.assertEqual(self._metadata().get("License-File"), ["LICENSE"])

    def test_the_console_script_is_in_the_wheel(self):
        entry_points = self.built["payload"]["entry_points"]
        self.assertIn("[console_scripts]", entry_points)
        self.assertIn(
            "roblox-studio-mcp = roblox_studio_mcp.extended_server:main",
            entry_points,
        )

    def test_the_landing_page_carries_the_disclosure(self):
        # CRLF-normalised first. A wheel built on Windows has `\r\n` throughout
        # its METADATA - measured 2026-10-09, `Metadata-Version: 2.4\r\nName: ...`
        # - so `partition("\n\n")` finds nothing and reads the description as the
        # empty string, while `partition("\r\n\r\n")` works. The content was
        # present in both build directions; only this reader was LF-only. The
        # assertion is about the disclosure, not the line endings, so the endings
        # are normalised rather than branched on. Same trap the box records:
        # line endings do not survive the crossing.
        metadata = self.built["payload"]["metadata"].replace("\r\n", "\n")
        description = metadata.partition("\n\n")[2]
        self.assertIn(
            "AI-written",
            description[:2000],
            "the PyPI landing page renders `readme`, which must state the "
            "project's AI authorship; a visitor never sees the repository README",
        )

    def test_the_declared_floor_survives_the_build(self):
        self.assertEqual(self._metadata().get("Requires-Python"), [">=3.9"])

    def test_every_skill_still_ships(self):
        """The `package-data` glob that moved the skills into the wheel is
        adjacent to everything in this file, so it is re-checked from the same
        build rather than trusted. `extended_skill` served an empty catalogue
        with no error once already."""
        names = self.built["payload"]["names"]
        skills = sorted(n for n in names if n.startswith("roblox_studio_mcp/skills/"))
        self.assertTrue(skills, "no skills in the wheel: %s" % names)
        self.assertTrue(all(n.endswith(".md") for n in skills))


if __name__ == "__main__":
    unittest.main()
