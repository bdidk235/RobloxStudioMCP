"""Every file pointer in this repo's own source resolves to a real tracked file.

Skills cite source constantly (``capture.py:377``), and a pointer that rots
points a reader at the wrong code with full confidence - the silent-wrong-answer
class in documentation form. This sweeps every reference written as a code span
- `` `capture.py:377` ``, `` `types.py:115-123` ``, `` `TODO.md` `` - across the
files that are load-bearing today and asserts each one names a file this
repository actually tracks, at a line that exists.

Scope
-----
``python/src/roblox_studio_mcp/skills/*.md`` (the skills), ``python/src/**/*.py``
and ``python/tests/*.py``. The first version of this gate scanned the skills
only, and that is exactly the hole this file closes: a pointer naming a report
that ``git log --all`` shows was never committed survived in
``extended/extensions.py`` and ``tests/test_array_escape.py``, each citing that
report as being in this repository. Nothing outside ``skills/`` was being read,
so nothing looked. Both now cite the ``rsx-transport`` skill, which is where the
finding actually lives. (No ``refs-ok`` marker here: the dead name is described
rather than written, so there is nothing to exempt.)

**``docs/*.md`` is measured out, not assumed out.** Run over
``docs/EVIDENCE.md`` and ``docs/HISTORY.md``, this rule reports **24** problems
against **154** clean references: 21 name a file that is gone (the parity
scripts, the Node surface measurement script, three deleted workflows, the
review transcripts) and 3 are past EOF (two README line ranges; the file is 203
lines now). Those two files are records anchored at the moment they were
written, so a stale name in them is history rather than a live defect, and
gating them would mean 24 escape markers in a log. One live exception is
recorded rather than gated: ``docs/EVIDENCE.md:369`` cites the acid-test script
one directory above where it lives.

How a reference is recognised
-----------------------------
A bare filename pattern is unusable here: over the scoped files it matches
**169** times and 30 of those resolve to nothing, because the matches are class
names, Luau samples, URL fragments and ordinary prose (``ARRAY_ESCAPE_NOTE`` is
a constant, not a file). Requiring the **code span** cuts that to **89** matches
with **5** unresolved - and it costs nothing, because all **13** line-numbered
pointers in scope sit inside a code span, so the two patterns agree exactly on
the subset that carries a line.

How "a real file" is decided
----------------------------
``git ls-files``, not the filesystem: a scratch file left on disk is not
something a pointer may cite, and the index is what a clone reproduces. A
reference resolving to an untracked path is a dead pointer, which is the other
half of the ``REQUEST-`` finding.

A reference the sweep cannot anchor in this repository is not judged. A bare
name is anchored through the module roots - the convention ``capture.py`` means
``extended/capture.py``, not a fixture with the same name. A multi-segment name
is anchored through the same roots first (skills write ``extended/capture.py``)
and then against the repository root. A multi-segment name whose first segment
is not a top-level entry of the tracked tree points *outside* this repository by
construction, and three live in the tree today:
``worships/roblox-fastlog-viewer/docs/fastlog.md`` in ``logid.py`` (an external
spec, quoted from directly), ``.config/opencode/AGENTS.md`` in
``test_docs_freshness.py`` (a host path) and
``.../roblox_studio_mcp/extended/skills.py`` in ``skills.py`` (a truncated
sketch). None of them claims to be a file here.

That is also how this file can talk about dead pointers at all: a reference
written without a code span is prose, and prose is not judged. The dead names
named above are deliberate.

Escape hatch
------------
A line carrying ``refs-ok: <reason>`` is skipped. The reason is enforced -
``_MARKER_REASON`` requires non-whitespace after the colon, so a bare marker is
itself a finding, and ``test_a_bare_marker_does_not_silence_a_pointer`` proves
it. ``# spelling-ok: <reason>`` in ``test_path_spelling_sweep.py`` is the
precedent, and the failure mode is the same: a marker without a reason lets the
next real violation through unexamined. There is no block form here - a marker
excuses exactly the line it sits on, so it cannot excuse a neighbour.

What this deliberately does NOT do: judge whether the cited line *supports* the
claim made about it. A skill can claim anything; no gate can check that, and the
obvious one provably cannot work - ``docs/EVIDENCE.md`` records why a
cross-file consistency check would have *rewarded* the failure it was built to
catch, since skill and code agreed with each other and were both wrong.
Well-formed pointers only, reachability only.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

#: This file is python/tests/test_skill_refs.py, so three levels up is the
#: repository root. (Third time this has bitten in this repo - see the note in
#: test_closed_sets.py.)
_REPO = pathlib.Path(__file__).resolve().parent.parent.parent

_PYTHON = _REPO / "python"
_PKG = _PYTHON / "src" / "roblox_studio_mcp"
_SKILLS = _PKG / "skills"
_TESTS = _PYTHON / "tests"

#: Suffixes this gate understands. Adding a family is a deliberate decision: it
#: makes more references checkable and more noise checkable in the same move.
_EXT = r"(?:py|md|toml|json|yml|txt|html)"

#: A file reference written as a code span. The lookbehind is the original
#: gate's guard against URLs and longer tokens; the closing span is what keeps
#: prose out, and it delimits the optional ``:12`` or ``:12-34`` line suffix, so
#: no trailing ``\b`` is needed.
_POINTER = re.compile(
    r"(?<![:/\w])"
    r"`([A-Za-z0-9_./-]+\." + _EXT + r")"
    r"(?::(\d+)(?:-(\d+))?)?"
    r"`"
)

_MARKER = "refs-ok:"  # refs-ok: defines the marker this gate honours

#: A marker only counts when it says why. ``\s+`` after the colon, so a marker
#: with nothing but a quote or a newline following it does not count.
_MARKER_REASON = re.compile(re.escape(_MARKER) + r"\s+\S")


def _dirs(base):
    """The folders the sweep covers, keyed by label."""
    python = pathlib.Path(base) / "python"
    package = python / "src" / "roblox_studio_mcp"
    return {
        "skills": package / "skills",
        "src": python / "src",
        "tests": python / "tests",
    }


def _roots(base):
    """Where a name is looked for, most specific first.

    The order is load-bearing and inherited from the skills-only gate: the
    skills folder is first, so a skill citing ``README.md`` means the skills
    README rather than the repository's.
    """
    base = pathlib.Path(base)
    python = base / "python"
    package = python / "src" / "roblox_studio_mcp"
    return [
        package / "skills",
        package / "extended",  # `capture.py` means the module, not a fixture
        package,
        python / "src",
        python,
        python / "tests",
        base,
        base / "contract",
        base / ".github" / "workflows",
        python / "scripts",
        base / "docs",
    ]


def _sources(base):
    """Every file the sweep reads, grouped by label, in a stable order."""
    dirs = _dirs(base)
    return {
        "skills": sorted(dirs["skills"].glob("*.md")),
        "src": sorted(dirs["src"].rglob("*.py")),
        "tests": sorted(dirs["tests"].glob("*.py")),
    }


def _tracked(base):
    """Every tracked file under ``base``, as repository-relative posix paths.

    ``git ls-files`` is the source of truth for "is a file in this repository":
    the filesystem answers a different question. Relative rather than absolute
    so that a *copy* of the tree reuses the identical set - which is what makes
    the planted-pointer control possible.

    Failing loudly beats passing quietly: a gate that cannot read the index must
    not report "clean".
    """
    base = pathlib.Path(base).resolve()
    done = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(base), capture_output=True, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(
            "git ls-files failed under %s (%s); the pointer gate needs the index"
            % (base, done.stderr.decode("utf-8", "replace").strip())
        )
    return {e for e in done.stdout.decode("utf-8").split("\0") if e}


def _points_outside(name, top):
    """True for a multi-segment name rooted outside this repository.

    ``worships/…/docs/fastlog.md``, ``.config/opencode/AGENTS.md`` and
    ``.../skills.py`` all live in the tree today and none claims to be a file
    here. ``docs/`` and ``.github/`` are top-level entries, so a path under
    either *is* judged.
    """
    return "/" in name and name.split("/")[0] not in top


def _resolve(name, base, roots):
    """The file a reference names, or None.

    Callers ask ``_points_outside`` first: a name rooted outside this repository
    is not a claim this gate can check, so it is not resolved on purpose.
    """
    if any(segment in (".", "..") for segment in name.split("/")):
        return None  # a relative escape, not a repository path
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    candidate = pathlib.Path(base) / name
    return candidate if candidate.is_file() else None


def _relative(target, base):
    """``target`` relative to ``base`` as posix, or None if not under it."""
    try:
        return target.resolve().relative_to(base).as_posix()
    except ValueError:
        return None


def _references(text):
    return list(_POINTER.finditer(text))


def _problems_in_text(text, where, base, tracked, roots, top):
    """Every pointer problem in one blob of text, one string per finding."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _MARKER in line:
            # The reason is load-bearing: without one the marker is a hole with
            # a name on it, so it is reported rather than honoured.
            if not _MARKER_REASON.search(line):
                found.append(f"{where}:{number}: {_MARKER} carries no reason")
            continue
        for match in _references(line):
            name, first, last = match.group(1), match.group(2), match.group(3)
            label = f"{where}:{number}"
            if _points_outside(name, top):
                continue
            target = _resolve(name, base, roots)
            if target is None:
                found.append(f"{label}: `{name}` names no file in this repository")
                continue
            relative = _relative(target, base)
            if relative is None or relative not in tracked:
                found.append(f"{label}: `{name}` names no tracked file")
                continue
            if first is not None:
                total = sum(1 for _ in target.open(encoding="utf-8"))
                end = int(last) if last is not None else int(first)
                if end > total:
                    found.append(
                        f"{label}: `{name}:{first}"
                        + (f"-{last}" if last else "")
                        + f"` is past line {total}"
                    )
    return found


def _sweep(base, tracked=None):
    """The gate: every pointer problem across every scoped file."""
    base = pathlib.Path(base).resolve()
    if tracked is None:
        tracked = _tracked(base)
    roots = _roots(base)
    top = {entry.split("/")[0] for entry in tracked}
    problems = []
    for paths in _sources(base).values():
        for path in paths:
            problems.extend(
                _problems_in_text(
                    path.read_text(encoding="utf-8"),
                    _relative(path, base) or path.name,
                    base,
                    tracked,
                    roots,
                    top,
                )
            )
    return problems


# The poisons below are read by the negative controls, not by a reader, so each
# one carries the escape hatch with the reason for it. Without the marker this
# file would fail its own gate - which is the point of writing them as literals
# rather than assembling them out of fragments: the hatch has to be exercised by
# something real, and the only line that legitimately needs it is one of these.
_GONE = "`nope.py`"  # refs-ok: poison for the missing-file control
_TOO_FAR = "`capture.py:999999`"  # refs-ok: poison for the out-of-range control

#: A correct pointer, deliberately unmarked: if a real reference ever needs a
#: marker, the marker is broken.
_JUST_RIGHT = "`capture.py:377`"

#: Copied wholesale by the planted-pointer control. The repository is small, so
#: the copy is not a performance question; it is a module-level constant rather
#: than a class attribute because a bound method would not match
#: ``shutil.copytree``'s calling convention.
_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")


class TestSkillPointersResolve(unittest.TestCase):
    """The sweep itself, run over the tree: it must find nothing."""

    def test_the_skills_folder_exists(self):
        # Without this, a moved or renamed folder makes the loop iterate over
        # nothing and the whole file passes vacuous.
        self.assertTrue(_SKILLS.is_dir(), f"no skills folder at {_SKILLS}")
        self.assertGreaterEqual(
            len(list(_SKILLS.glob("*.md"))), 5, f"{_SKILLS} holds too few skills"
        )

    def test_every_scanned_folder_exists_and_is_not_empty(self):
        # The same vacuity guard, for the two folders the widened gate added. A
        # renamed directory must not silently shrink the sweep to nothing.
        dirs = _dirs(_REPO)
        self.assertGreaterEqual(
            len(list(dirs["src"].rglob("*.py"))), 20, f"{dirs['src']} too few modules"
        )
        self.assertGreaterEqual(
            len(list(dirs["tests"].glob("*.py"))), 20, f"{dirs['tests']} too few tests"
        )

    def test_the_sweep_actually_sees_references(self):
        """A pointer gate that matches nothing passes vacuously.

        The first draft of this file carried a regex whose line-number group had
        lost its optional flag: it matched a handful of references across the
        whole tree and reported clean. A floor on the match count turns that
        class of typo into a red suite instead of a green one watching nothing.
        """
        total = 0
        for paths in _sources(_REPO).values():
            for path in paths:
                total += len(_references(path.read_text(encoding="utf-8")))
        self.assertGreaterEqual(
            total, 40, f"only {total} code-span references; the pattern is broken"
        )

    def test_every_pointer_names_a_real_tracked_file(self):
        problems = _sweep(_REPO)
        self.assertEqual(problems, [], "\n".join(problems))


class TestThePointerGateCanActuallyFail(unittest.TestCase):
    """Negative controls: the gate must be seen to fail.

    ``TODO.md`` claimed this gate was "mutation-proven by planting a bad
    pointer" while no such test existed. The claim and the proving are both here
    now. Each control drives the real predicate with a poison and asserts it
    rejects it, and each poison has a passing counterpart, so none of these can
    be satisfied by a check that is simply always-false.
    """

    def setUp(self):
        self._tracked = _tracked(_REPO)
        self._top = {e.split("/")[0] for e in self._tracked}

    def _problems(self, text):
        return _problems_in_text(
            text, "poison", _REPO, self._tracked, _roots(_REPO), self._top
        )

    def test_a_missing_file_is_reported(self):
        problems = self._problems(_GONE)
        self.assertTrue(problems, "a pointer to a file that does not exist passed")
        self.assertIn("names no file", problems[0])

    def test_an_out_of_range_line_is_reported(self):
        problems = self._problems(_TOO_FAR)
        self.assertTrue(problems, "a pointer past the end of a real file passed")
        self.assertIn("past line", problems[0])

    def test_a_correct_pointer_is_not_reported(self):
        # The counterpart. Without it the two controls above are satisfied by a
        # checker that flags everything.
        self.assertEqual(self._problems(_JUST_RIGHT), [])
        self.assertEqual(self._problems("`types.py:115-123` and `TODO.md`"), [])

    def test_a_bare_marker_does_not_silence_a_pointer(self):
        # The colon is concatenated so this source line does not itself read as
        # a marker: what carries the bare marker is the *value*.
        bare = _GONE + " refs-ok" + ":"
        problems = self._problems(bare)
        self.assertTrue(problems, "an escape marker with no reason silenced a pointer")
        self.assertIn("carries no reason", problems[0])

    def test_a_marker_with_a_reason_is_honoured(self):
        self.assertEqual(self._problems(_GONE + "  # refs-ok: measured, external"), [])

    def test_prose_is_not_a_pointer(self):
        """What must stay quiet, or the gate is noise nobody reads.

        Every one of these is a reason the bare-pattern gate could not be
        widened as it stood: class names, samples, and paths that are not
        claims about this repository.
        """
        for prose in (
            "class ARRAY_ESCAPE_NOTE ...",
            "ARRAY_ESCAPE_NOTE, WatchResult and _annotate_array_escape.",
            "A 165-row driver returned {} with no error.",
            # A URL keeps its scheme, and the lookbehind refuses it.
            "`https://example.com/docs/spec.md`",
            # Rooted outside this repository: not a claim about it.
            "`example.com/docs/spec.md`",
        ):
            with self.subTest(prose=prose[:40]):
                self.assertEqual(self._problems(prose), [], prose)


class TestAPlantedPointerFailsTheRealSweep(unittest.TestCase):
    """The controls above drive the predicate. This one drives the gate.

    A copy of the tree with a bad pointer planted in it: discovery, code-span
    matching, resolution, the tracked set and line counting all engaged. Those
    predicate controls alone would not have caught a sweep reading the wrong
    folder or counting the wrong file's lines.
    """

    _VICTIM = "python/tests/test_array_escape.py"

    def test_a_planted_pointer_makes_the_sweep_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "repo"
            shutil.copytree(_REPO, base, ignore=_COPY_IGNORE)
            # Relative tracked paths survive the copy unchanged - which is why
            # _tracked returns them relative rather than absolute.
            tracked = _tracked(_REPO)
            self.assertEqual(_sweep(base, tracked), [], "the untouched copy must pass")

            victim = base / self._VICTIM
            original = victim.read_text(encoding="utf-8")
            try:
                for label, poison in (
                    ("a missing file", _GONE),
                    ("an out-of-range line", _TOO_FAR),
                    # And the counterpart: a correct pointer must not trip it.
                    ("a correct pointer", _JUST_RIGHT),
                ):
                    with self.subTest(planted=label):
                        victim.write_text(
                            original + "\nPlanted " + poison + "\n", encoding="utf-8"
                        )
                        problems = [
                            p for p in _sweep(base, tracked) if self._VICTIM in p
                        ]
                        if label == "a correct pointer":
                            self.assertEqual(
                                problems, [], f"a correct pointer was flagged: {problems}"
                            )
                        else:
                            self.assertTrue(
                                problems, f"planting {label} did not make the sweep fail"
                            )
            finally:
                victim.write_text(original, encoding="utf-8")

            self.assertEqual(
                _sweep(base, tracked), [], "restoring the file did not clear the sweep"
            )


if __name__ == "__main__":
    unittest.main()
