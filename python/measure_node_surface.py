"""Measure Node's extended tool surface against the Python budget.

Node and Python are supposed to be interchangeable, but only Python enforces the
description budget. This reports both so the gap is a number rather than an
impression.
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
NODE = ROOT / "node" / "src" / "extendedServer.ts"
PY_TOTAL_CAP = 2700
PY_PER_TOOL_CAP = 450

src = NODE.read_text(encoding="utf-8")

# Each literal looks like:  "extended_x",\n    "description text",
pairs = re.findall(
    r'"(extended_[a-z_]+)",\s*\n\s*"((?:[^"\\]|\\.)*)"',
    src,
)
seen = {}
for name, desc in pairs:
    seen[name] = desc

print("Node extended tool literals: %d" % len(seen))
for name, desc in sorted(seen.items(), key=lambda kv: -len(kv[1])):
    flag = "  <-- OVER per-tool cap" if len(desc) > PY_PER_TOOL_CAP else ""
    print("  %4d  %s%s" % (len(desc), name, flag))
total = sum(len(d) for d in seen.values())
print("Node total: %d chars over %d tools" % (total, len(seen)))
print("Python budget: total <= %d, per tool <= %d" % (PY_TOTAL_CAP, PY_PER_TOOL_CAP))
print("Node over total cap by: %d" % (total - PY_TOTAL_CAP))

sys.path.insert(0, str(ROOT / "python" / "src"))
try:
    from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS
    py = {t.name: len(t.description) for t in _EXTENDED_TOOLS}
    print("\nPython tools: %d, total %d" % (len(py), sum(py.values())))
    print("In Python but not Node: %s" % sorted(set(py) - set(seen)))
    print("In Node but not Python: %s" % sorted(set(seen) - set(py)))
    for name in sorted(set(py) & set(seen)):
        if abs(py[name] - len(seen[name])) > 20:
            print("  description drift %-32s python %4d  node %4d"
                  % (name, py[name], len(seen[name])))
except Exception as exc:  # noqa: BLE001 - this is a reporting script
    print("python import failed: %r" % (exc,))
