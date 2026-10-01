"""The shared Python/Node tool contract, generated from the Python side.

Why a generated file rather than a hand-written list
---------------------------------------------------
Parity between two implementations is the kind of thing that is *believed*
rather than enforced. Both sides had drifted - Node was missing three tools, and
six schemas differed - with nothing to notice, because each side's tests only
checked its own tools.

So the contract is a file, both test suites read it, and neither can drift
without a test failing. Regenerating it is a deliberate act:

    python parity/build_contract.py

and the diff in the commit message is then the parity report.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTRACT = os.path.join(HERE, "tools.json")

sys.path.insert(0, os.path.join(ROOT, "python", "src"))

#: Relayed Studio tools that have a better extended_* equivalent, and the
#: steer appended to each tool's own description.
#:
#: **Why the relayed tool's description and not a skill.** The model decides
#: which tool to call while reading the tool list, and the list is the only
#: always-on surface. A skill is opt-in, so a trap that lives only there has
#: already been chosen by the time anyone reads it - which is why
#: `screen_capture` capturing the wrong Studio was invisible: the trap was in
#: `rsx-capture`, not where the choice gets made.
#:
#: Every entry below is a **measured** reason, not a preference:
#:
#: ``multi_edit``      - fails the entire call if any one edit is invalid, and
#:   does not read the target first, so an edit against a stale read silently
#:   writes the wrong file.
#: ``screen_capture``  - JPEG only, and it smears 1px edges. It also does not
#:   name the ``studio_id`` it captured, whereas ``extended_capture`` returns it
#:   and can write to a file instead of shipping megabytes. (An earlier entry
#:   here claimed this tool took an *optional* ``studio_id`` and silently
#:   captured the wrong Studio. Measured 2026-10-01: the schema **requires**
#:   ``studio_id`` and refuses the call without it, so that hazard does not
#:   occur. The entry was corrected rather than kept, because a steer that
#:   names a failure the model would otherwise walk into is worth nothing once
#:   the failure cannot happen.)
#: ``script_grep``     - returns no context lines, so every hit costs a
#:   follow-up ``script_read`` to see the match.
#: ``get_console_output`` - returns the whole buffer every time, so polling it
#:   re-ships everything already seen. ``extended_watch_output`` returns only
#:   lines since the last call.
#: ``script_search``   - returns paths only; reading each one is a separate call.
#: ``execute_luau``    - fine for short snippets. Only steer when the code is
#:   long enough to be awkward to inline.
#:
#: Kept deliberately small. Every entry is permanent cost on every session, so
#: a steer must name a **failure the model would otherwise walk into**, not
#: merely a tool that exists. Notes are terse for the same reason.
_STEERS: Tuple[Tuple[str, str], ...] = (
    (
        "multi_edit",
        "NOTE: prefer extended_write_script for a full-file replacement, or "
        "extended_update_script for targeted edits - this fails the whole call "
        "if any one edit is invalid, and does not read the target first.",
    ),
    (
        "screen_capture",
        "NOTE: prefer extended_capture when the pixels are the measurement - "
        "this is JPEG (smears 1px edges); extended_capture names the studio_id "
        "it captured and can save to a file instead of returning megabytes.",
    ),
    (
        "script_grep",
        "NOTE: prefer extended_script_grep - this returns no context lines, so "
        "each hit costs a separate script_read to see.",
    ),
    (
        "get_console_output",
        "NOTE: prefer extended_watch_output when polling - this returns the "
        "whole buffer every time, re-sending lines you already have.",
    ),
    (
        "script_search",
        "NOTE: prefer extended_script_search_and_read to also batch-read the "
        "sources; this returns paths only.",
    ),
)


#: Per-tool and total description caps. Enforced on both sides; see the
#: "Remaining" section of TODO.md for why the total is the binding one.
PER_TOOL_CAP = 450
#: Raised from 2,700 on 2026-10-01. The old cap forced a choice between saying
#: what an action *returns* and staying under budget, which is the wrong trade:
#: an agent that does not know a launch hands back a `studio_id` will make a
#: second call to find one, and the cap was dictating tool behaviour. What the
#: cap is for is preventing prose that is not what/when/why - and that is
#: enforced by reading each description, not by starving them. The list is paid
#: on every call of every session, so the number still matters; it now covers
#: the information needed to *use* a tool, not only to recognise one.
TOTAL_CAP = 3200


def steers() -> List[Dict[str, str]]:
    """The relay-to-extended steering table, in the contract.

    Lives here rather than in either implementation so it is generated once and
    asserted by both suites, exactly like the tool surface. Two hand-written
    copies of this table would drift, and the failure mode is a model quietly
    choosing the worse tool - invisible, because the worse tool still works.
    """
    return [{"name": name, "note": note} for name, note in _STEERS]


def _normalise_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a schema to the parts that must match across implementations.

    Only the *shape* is contractual: the property names, their JSON types, which
    are required, and the read-only hint. Wording differs legitimately - the
    descriptions are separately budgeted, not required to be identical - and
    defaults are recorded but not compared, because one side may legitimately
    spell a default differently while behaving the same.

    `read_only` was added after measuring that Python marked 7 tools read-only and
    Node marked **none**, with nothing able to notice. The hint is what tells a
    client a tool only observes, so it may run those in parallel; unmarked is read
    as "may mutate", so omitting it is *safe* but needlessly serial. A client that
    grants parallel execution from the hint is the reason it has to match.

    What this deliberately does **not** do is compare implementation internals.
    The point is that a caller can use either server, not that the code matches.
    """
    properties = {}
    for name, spec in (schema.get("properties") or {}).items():
        entry: Dict[str, Any] = {"type": spec.get("type")}
        if "items" in spec:
            entry["items_type"] = (spec.get("items") or {}).get("type")
        if "default" in spec:
            entry["has_default"] = True
        if spec.get("enum"):
            entry["enum"] = list(spec["enum"])
        properties[name] = entry
    return {
        "properties": properties,
        "required": sorted(schema.get("required") or []),
    }


def build() -> Dict[str, Any]:
    from roblox_studio_mcp.extended_server import _EXTENDED_TOOLS

    tools: List[Dict[str, Any]] = []
    for tool in sorted(_EXTENDED_TOOLS, key=lambda t: t.name):
        tools.append(
            {
                "name": tool.name,
                "description_chars": len(tool.description),
                "read_only": bool(getattr(tool, "read_only", False)),
                "schema": _normalise_schema(tool.input_schema),
            }
        )

    total = sum(t["description_chars"] for t in tools)
    return {
        "_comment": (
            "Generated by parity/build_contract.py from the Python server. "
            "Both test suites assert against this file; do not hand-edit. "
            "Regenerate when the tool surface changes on purpose."
        ),
        "per_tool_description_cap": PER_TOOL_CAP,
        "total_description_cap": TOTAL_CAP,
        "total_description_chars": total,
        "tool_count": len(tools),
        "steers": steers(),
        "tools": tools,
    }


def main() -> int:
    contract = build()
    with open(CONTRACT, "w", encoding="utf-8") as handle:
        json.dump(contract, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(
        "wrote %s: %d tools, %d/%d description chars"
        % (
            os.path.relpath(CONTRACT, ROOT),
            contract["tool_count"],
            contract["total_description_chars"],
            contract["total_description_cap"],
        )
    )
    over = [
        t["name"]
        for t in contract["tools"]
        if t["description_chars"] > contract["per_tool_description_cap"]
    ]
    if over:
        print("WARNING: over the per-tool cap: %s" % ", ".join(over))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
