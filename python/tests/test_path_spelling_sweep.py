"""Sweep the suite for assertions on a path's spelling, not its file.

954e07a fixed three tests that asserted on the *spelling* of a path rather
than the file it names: they passed on a Windows dev box (where ``tempfile``
hands back an already-resolved path) and failed on both CI runners
(``RUNNER~1`` short form on Windows, ``/private/var`` on macOS). The instances
are closed; this test keeps the class closed.

The rule: an assertion comparing a product path against an expected path must
compare resolved forms, because resolution (short-name expansion, ``/private``
symlinks, ``~``) differs by machine. A hardcoded absolute path on an assert
line is the smell - fixture *inputs* elsewhere in the file are fine, which is
why this scans assert lines only.

Escape hatch: an assert line carrying ``# spelling-ok: <reason>`` - on the
line itself or in the comment block directly above it - is skipped. The
reason is load-bearing - a bare marker would let the next spelling assertion
through unexamined.
"""

import pathlib
import re
import unittest

# spelling-ok: pattern definitions, not assertions.
_PATH_LITERAL = re.compile(r"C:[\\/]|/private/|~1\b")
_ASSERT = re.compile(r"^\s*(self\.)?assert\w*\(.*")


def _offenders(path: pathlib.Path):
    found = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if "spelling-ok:" in line:
            continue
        if not (_ASSERT.match(line) and _PATH_LITERAL.search(line)):
            continue
        # The reason lives above the code it justifies: a marker in the
        # preceding comment block covers the assert.
        context = "\n".join(lines[max(0, number - 4) : number - 1])
        if "spelling-ok:" in context:
            continue
        found.append(f"{path.name}:{number}: {line.strip()}")
    return found


class TestNoSpellingAssertions(unittest.TestCase):
    def test_no_assert_line_names_a_path_by_spelling(self):
        """Every assert line with a path literal must carry its reason."""
        tests_dir = pathlib.Path(__file__).parent
        offenders = []
        for candidate in sorted(tests_dir.glob("test_*.py")):
            offenders.extend(_offenders(candidate))
        self.assertEqual(
            offenders,
            [],
            "assertions on path spellings (compare resolved forms instead):\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
