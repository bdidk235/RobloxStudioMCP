"""Every file:line pointer in a skill resolves to a real line.

Skills cite source constantly (``capture.py:377``), and a pointer that rots
points a reader at the wrong code with full confidence - the silent-wrong-answer
class in documentation form. This scans ``skills/*.md`` for ``path`` and
``path:line[-line]`` references and asserts each file exists and each line is
in range.

What this deliberately does NOT do: judge whether the cited line *supports*
the claim made about it. A skill can claim anything; no gate can check that,
and the obvious one provably cannot work. Well-formed pointers only.
"""

import os
import pathlib
import re
import unittest

_SKILLS = pathlib.Path(__file__).parent.parent.parent / "skills"
_SRC = _SKILLS.parent / "python" / "src" / "roblox_studio_mcp"

# Bare filenames resolve here first: ``capture.py`` means the module, not a
# coincidentally-named test fixture.
_ROOTS = [
    _SKILLS,
    _SRC / "extended",
    _SRC,
    _SKILLS.parent / "python",
    _SKILLS.parent / "python" / "tests",
    _SKILLS.parent,
]

_REF = re.compile(
    r"(?<![/\w:])"  # not part of a URL or a longer token
    r"([A-Za-z0-9_./-]+\.(?:py|md|toml|json|yml|txt|html))"
    r"(?::(\d+)(?:-(\d+))?)?"
    r"\b"
)


def _resolve(name):
    for root in _ROOTS:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _problems(text_file):
    found = []
    text = text_file.read_text(encoding="utf-8")
    for match in _REF.finditer(text):
        name, first, last = match.group(1), match.group(2), match.group(3)
        target = _resolve(name)
        if target is None:
            found.append(f"{text_file.name}: {name} names no file")
            continue
        if first is not None:
            total = sum(1 for _ in target.open(encoding="utf-8"))
            end = int(last) if last is not None else int(first)
            if end > total:
                found.append(
                    f"{text_file.name}: {name}:{first}"
                    + (f"-{last}" if last else "")
                    + f" is past line {total}"
                )
    return found


class TestSkillPointersResolve(unittest.TestCase):
    def test_every_file_line_pointer_names_a_real_line(self):
        problems = []
        for skill in sorted(_SKILLS.glob("*.md")):
            problems.extend(_problems(skill))
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
