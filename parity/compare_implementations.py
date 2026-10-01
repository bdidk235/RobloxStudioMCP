"""Compare the two implementations on facts, not impressions.

Answers three questions with numbers:
  1. What does each side have that the other does not?
  2. How much code is each side spending on the same surface?
  3. Which risks are one-sided?

Deliberately not a quality judgement. The point is to make the differences
legible so they can be decided about, rather than argued about from memory.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_EXT = os.path.join(ROOT, "python", "src", "roblox_studio_mcp", "extended")
NODE_EXT = os.path.join(ROOT, "node", "src", "extended")


def module_name(filename: str) -> str:
    return os.path.splitext(filename)[0]


def inventory(directory: str, ext: str) -> Dict[str, Tuple[int, int]]:
    """name -> (bytes, lines) for each source file in `directory`."""
    out: Dict[str, Tuple[int, int]] = {}
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(ext):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        out[module_name(name)] = (os.path.getsize(path), text.count("\n") + 1)
    return out


def rule(title: str) -> None:
    print("\n" + title)
    print("-" * len(title))


py = inventory(PY_EXT, ".py")
node = inventory(NODE_EXT, ".ts")

rule("1. MODULE INVENTORY  (bytes / lines)")
names = sorted(set(py) | set(node))
print("%-16s %14s %14s   %s" % ("module", "python", "node", "note"))
for name in names:
    p = py.get(name)
    n = node.get(name)
    fmt = lambda v: ("%6d B %5d L" % v) if v else " " * 15 + "  -   "
    note = ""
    if p and not n:
        note = "PYTHON ONLY"
    elif n and not p:
        note = "node only"
    print("%-16s %s %s   %s" % (name, fmt(p), fmt(n), note))

rule("2. TOTALS")
py_bytes = sum(v[0] for v in py.values())
py_lines = sum(v[1] for v in py.values())
node_bytes = sum(v[0] for v in node.values())
node_lines = sum(v[1] for v in node.values())
print("python extended/ : %6d B  %5d lines  (%d modules)" % (py_bytes, py_lines, len(py)))
print("node   extended/ : %6d B  %5d lines  (%d modules)" % (node_bytes, node_lines, len(node)))
print("ratio            : %.2fx" % (py_bytes / node_bytes if node_bytes else 0))

rule("3. WHAT ONLY ONE SIDE HAS")
only_py = sorted(set(py) - set(node))
only_node = sorted(set(node) - set(py))
print("python only: %s" % (", ".join(only_py) or "none"))
print("node only  : %s" % (", ".join(only_node) or "none"))
gap_bytes = sum(py[k][0] for k in only_py)
gap_lines = sum(py[k][1] for k in only_py)
print("python-only weight: %d B / %d lines unported" % (gap_bytes, gap_lines))

rule("4. TEST INVENTORY")
py_tests = inventory(os.path.join(ROOT, "python", "tests"), ".py")
node_tests = inventory(os.path.join(ROOT, "node", "tests"), ".ts")


def canonical(name: str) -> str:
    """Normalise the two naming conventions so they can be compared.

    Without this the whole suite reads as "only on one side", which is a
    difference in filename style, not in coverage - and it hid the real gap the
    first time this script was run.
    """
    stem = name
    if stem.endswith(".test"):
        stem = stem[: -len(".test")]
    if stem.startswith("test_"):
        stem = stem[len("test_"):]
    stem = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    if stem == "integration_studio":
        stem = "integration"
    return stem


py_by = {canonical(k): k for k in py_tests}
node_by = {canonical(k): k for k in node_tests}

py_test_lines = sum(v[1] for k, v in py_tests.items() if k != "__init__")
node_test_lines = sum(v[1] for k, v in node_tests.items())
print("python tests/ : %5d lines in %d files" % (py_test_lines, len(py_tests)))
print("node   tests/ : %5d lines in %d files" % (node_test_lines, len(node_tests)))
print("\ncoverage compared by subject (names normalised):")
only_py = sorted(set(py_by) - set(node_by))
only_node = sorted(set(node_by) - set(py_by))
shared = sorted(set(py_by) & set(node_by))
for name in only_py:
    print("  PYTHON ONLY : %-24s %4d L" % (name, py_tests[py_by[name]][1]))
for name in only_node:
    print("  node only   : %-24s %4d L" % (name, node_tests[node_by[name]][1]))
print("  both        : %s" % ", ".join(shared))
unported_test_lines = sum(py_tests[py_by[n]][1] for n in only_py)
print("\npython-only test lines: %d - these subjects have no Node coverage"
      % unported_test_lines)

rule("5. NODE BUILD OUTPUT IS THE REAL RISK")
dist = os.path.join(ROOT, "node", "dist")
if os.path.isdir(dist):
    newest_src = max(
        (os.path.getmtime(os.path.join(NODE_EXT, f)) for f in os.listdir(NODE_EXT)),
        default=0,
    )
    stale = []
    for dirpath, _dirs, files in os.walk(dist):
        for f in files:
            if f.endswith((".js", ".d.ts")):
                p = os.path.join(dirpath, f)
                if os.path.getmtime(p) < newest_src:
                    stale.append(os.path.relpath(p, ROOT))
    print("node/dist exists.")
    if stale:
        print("STALE: %d built file(s) older than the newest source:" % len(stale))
        for s in sorted(stale)[:8]:
            print("   %s" % s)
        print("-> Anything run from dist/ is running OLD code, including")
        print("   everything ported in this session.")
    else:
        print("dist is current with respect to extended/*.ts sources.")
else:
    print("node/dist does not exist - the Node server has never been built here.")

rule("6. LANGUAGE-LEVEL RISKS THAT ARE ONE-SIDED")
risks = [
    (
        "regex dialect",
        "Python `re` and JS `RegExp` differ: `\\S`, lookbehind and named-group\n"
        "      syntax are not identical. A ported pattern can compile in both and\n"
        "      still match differently.",
        "shared risk",
    ),
    (
        "split() semantics",
        "Python's `split(None, n)` KEEPS the remainder in the last field;\n"
        "      JavaScript's `split(re, n)` TRUNCATES it. This broke the macOS\n"
        "      process parser in this session - every row came back null.",
        "node exposed it",
    ),
    (
        "integer coercion",
        "`int(x)` truncates silently; `Number(x)` yields NaN for junk. A cap of\n"
        "      'lots' becomes a TypeError in Python and NaN in JS.",
        "node must check explicitly",
    ),
    (
        "falsy zero",
        "Identical in both - and both got it wrong in the same place:\n"
        "      `max_lines: 0` became 200 via `or 200` / `?? 200` asymmetry.",
        "shared, now fixed both",
    ),
    (
        "process spawning",
        "A PowerShell spawn costs ~1.9s measured on this machine. Python's\n"
        "      subprocess and Node's execFileSync are equally slow, so neither\n"
        "      implementation can make PowerShell cheap - only avoid it.",
        "shared",
    ),
    (
        "subclass identity",
        "TypeScript downlevelled to ES5 breaks `instanceof` on Error subclasses\n"
        "      without `setPrototypeOf`. Python has no equivalent hazard.",
        "node only",
    ),
    (
        "event loop / timers",
        "Identical, and the important detail is that the MCP *server* is a real\n"
        "      process with real timers in both. The 'cannot sleep' constraint is\n"
        "      about the agent's sandbox, not the server.",
        "shared",
    ),
]
for name, detail, tag in risks:
    print("* %s  [%s]" % (name, tag))
    print("      %s" % detail)

rule("7. STARTUP / OPERATIONAL")
try:
    out = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
            "Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine | "
            "ConvertTo-Json -Compress",
        ],
        capture_output=True, text=True, timeout=60,
    )
    import json as _json

    raw = out.stdout or ""
    # Earliest of `[` or `{`, not the latest: PowerShell's banner can contain a
    # brace, and taking max() picked one from inside the payload.
    candidates = [i for i in (raw.find("["), raw.find("{")) if i >= 0]
    start = min(candidates) if candidates else -1
    data = _json.loads(raw[start:]) if start >= 0 else None
    rows = data if isinstance(data, list) else ([data] if data else [])
    print("node.exe processes: %d" % len(rows))
    for row in rows:
        cmd = str(row.get("CommandLine", ""))
        # Classify by what it is actually running, not by whether it exists.
        if "extendedServer" in cmd or "roblox" in cmd.lower():
            kind = "MCP server"
        elif "vitest" in cmd:
            kind = "test run"
        elif not cmd.strip():
            kind = "no command line"
        else:
            kind = "other"
        print("  pid %-6s parent %-6s  %s" % (row.get("ProcessId"), row.get("ParentProcessId"), kind))
        print("      %s" % cmd[:150])
    if not rows:
        print("  (none - the Node MCP server is not currently running; the")
        print("   configured server is the Python one)")
except Exception as exc:  # noqa: BLE001
    print("could not inspect node.exe: %r" % (exc,))

sys.stdout.flush()
