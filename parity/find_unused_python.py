"""Find Python code that nothing uses, and separate real dead code from noise.

The distinction matters, because three different things all look like "unused":

1. **Genuinely dead** - defined, referenced nowhere. Safe to delete.
2. **Test-only** - unused by the server, but a test pins it. Often a
   deliberately-kept seam; deleting it deletes the test.
3. **Re-exported** - appears "unused" inside its own module because another
   module imports it. Counting references per-module instead of repo-wide
   mistakes these for dead code.

So this counts references **across the whole repo**, then reports where the only
other hit is a test, which is category 2 rather than 1.

Deliberately noisy about *why* rather than trying to be clever about *what*: a
symbol that is only ever mentioned in its own docstring is reported, because
that is the shape of a leftover.
"""

from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from typing import Dict, List, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "python", "src")
TESTS = os.path.join(ROOT, "python", "tests")
EXTRA = [os.path.join(ROOT, "parity"), os.path.join(ROOT, "skills")]

SKIP_DIRS = {"__pycache__", "node_modules", ".git", "dist"}


def python_files(*roots: str) -> List[str]:
    out: List[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py"):
                    out.append(os.path.join(dirpath, name))
    return sorted(out)


def module_level_symbols(path: str) -> List[Tuple[str, str, int]]:
    """(kind, name, lineno) for module-level defs, classes and assignments."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    found: List[Tuple[str, str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(("def", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            found.append(("class", node.name, node.lineno))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found.append(("const", target.id, node.lineno))
    return found


def main() -> int:
    src_files = python_files(SRC)
    test_files = python_files(TESTS, *EXTRA)
    all_files = src_files + test_files

    # One read per file, reused for every symbol: the naive version of this
    # re-reads the repo per symbol and takes minutes.
    contents: Dict[str, str] = {}
    for path in all_files:
        with open(path, encoding="utf-8", errors="replace") as handle:
            contents[path] = handle.read()

    src_set = set(src_files)

    dead: List[str] = []
    test_only: List[str] = []
    total = 0

    for path in src_files:
        rel = os.path.relpath(path, ROOT)
        for kind, name, lineno in module_level_symbols(path):
            if name.startswith("__") and name.endswith("__"):
                continue
            total += 1
            pattern = re.compile(r"\b%s\b" % re.escape(name))
            # Count occurrences rather than files, and count the DEFINING file.
            #
            # The first version of this subtracted the defining module from the
            # hit set, which reported 145 symbols "dead" including `_PID_RE` and
            # `_bounded_int` - both used inside their own module, which is where
            # nearly every private helper is used. A symbol is used if it appears
            # anywhere other than its own definition line.
            src_uses = 0
            test_uses = 0
            for other, text in contents.items():
                n = len(pattern.findall(text))
                if not n:
                    continue
                if other == path:
                    n -= 1  # drop the definition itself
                if n <= 0:
                    continue
                if other in src_set:
                    src_uses += n
                else:
                    test_uses += n

            where = "%-52s %-6s %-26s line %d" % (rel, kind, name, lineno)
            if src_uses:
                continue
            if test_uses:
                test_only.append(where + "  (%d test refs)" % test_uses)
            else:
                dead.append(where)

    print("PYTHON: UNUSED SYMBOLS")
    print("=" * 78)
    print("scanned %d source files, %d module-level symbols\n" % (len(src_files), total))

    print("1. DEAD - defined, referenced nowhere in src, tests or parity  (%d)" % len(dead))
    for line in sorted(dead):
        print("   " + line)

    print("\n2. TEST-ONLY - no source user, but a test pins it  (%d)" % len(test_only))
    for line in sorted(test_only):
        print("   " + line)
    print(
        "\n   These are usually deliberate: a seam kept alive by a test, or a\n"
        "   constant documenting an invariant. Deleting one deletes its test."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
