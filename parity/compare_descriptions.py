"""Report each tool's raw description length on both sides, side by side.

The parity contract compares *normalised* descriptions, so it stays green while
the two sides drift apart in raw length - and the budget is charged on raw
length. That gap is how the total can pass on one side and fail on the other
from the same edit.

Prints only the rows that disagree, plus the two totals. Not a gate; a
diagnostic for when the budget test fails on one side only.

    python parity/compare_descriptions.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def python_descriptions() -> Dict[str, str]:
    sys.path.insert(0, os.path.join(ROOT, "python", "src"))
    from roblox_studio_mcp import extended_server as es  # noqa: PLC0415

    return {tool.name: tool.description for tool in es._EXTENDED_TOOLS}


def node_descriptions() -> Dict[str, str]:
    """Pull `name:` / `description:` pairs out of the compiled JS.

    Parsed with a regex rather than by importing, because the values are string
    concatenations across lines and the goal is the *literal source text* - the
    same thing the budget is charged for.
    """
    path = os.path.join(ROOT, "node", "dist", "extendedServer.js")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    found: Dict[str, str] = {}
    pattern = re.compile(
        r'name:\s*"(?P<name>[^"]+)",\s*description:\s*(?P<desc>"(?:[^"\\]|\\.)*")'
        r'(?:\s*\+\s*(?:"(?:[^"\\]|\\.)*"))*',
        re.S,
    )
    for match in pattern.finditer(source):
        # Rejoin the `+ "..."` pieces the regex captured as one blob.
        raw = match.group("desc")
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(0)[match.group(0).index(raw):])
        found[match.group("name")] = "".join(pieces).replace('\\"', '"').replace("\\n", "\n")
    return found


def main() -> int:
    py = python_descriptions()
    node = node_descriptions()

    if set(py) != set(node):
        print("TOOL SET DIFFERS")
        print("  python only:", sorted(set(py) - set(node)))
        print("  node only:  ", sorted(set(node) - set(py)))
        return 1

    rows: list[Tuple[str, int, int]] = []
    for name in sorted(py):
        a, b = len(py[name]), len(node[name])
        if a != b:
            rows.append((name, a, b))

    total_py = sum(len(v) for v in py.values())
    total_node = sum(len(v) for v in node.values())
    print("TOOLS: %d   python total %d   node total %d   delta %+d"
          % (len(py), total_py, total_node, total_node - total_py))
    if not rows:
        print("every description is the same length on both sides")
        return 0

    print("\n%-32s %6s %6s %6s" % ("tool", "python", "node", "delta"))
    for name, a, b in rows:
        print("%-32s %6d %6d %+6d" % (name, a, b, b - a))
    print(
        "\nThe budget charges raw length, so a difference here is a difference "
        "in what the caller pays."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())