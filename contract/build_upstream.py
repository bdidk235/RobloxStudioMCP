"""The upstream tool surface, generated from a live ``tools/list`` capture.

Why a generated file rather than a hand-written list
----------------------------------------------------
`docs/catalog.js` hand-carries the upstream tools for the docs site, and on
2026-10-08 it matched a live ``tools/list`` exactly - unasserted, by luck. The
same "believed, not enforced" failure `contract/build_contract.py` was written
for, on the half of the tool surface nobody owns: a Studio update that renames
or drops a relayed tool reaches users as a runtime failure, never as a red
build.

So the upstream surface lives here, generated from a capture, and the suite
pins it (`python/tests/test_upstream_surface.py`). Two files, written together
by a live run:

* ``contract/upstream_live_dump.json`` - the raw capture, so regeneration is a
  pure function of checked-in inputs. This is what
  ``test_contract.py::RegenerationReproducesTheCommittedContract`` needed and
  lacked: without the raw dump, the snapshot's own inputs are unpinned and the
  generator can disagree with the file it ships.
* ``contract/upstream_tools.json`` - the normalised snapshot the doctor and
  the tests compare against.

Refresh
-------
After a Studio update, or any time the doctor reports drift::

    python contract/build_upstream.py --live

This launches the Studio MCP proxy (read-only: ``tools/list`` and the
handshake, nothing else), captures the list, and writes both files. The diff
in the commit is the contract report, exactly as for ``tools.json``.

Offline, the snapshot can be rebuilt from the checked-in dump (what the test
does)::

    python contract/build_upstream.py --from contract/upstream_live_dump.json

The two surfaces are graded separately on purpose: this one is owned by Roblox
Studio, so a live list may differ legitimately (additive tools, reworded
descriptions, a flaky uplink). The grading policy is
``roblox_studio_mcp.upstream.compare_snapshot`` - one implementation, shared
with the doctor and the tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SNAPSHOT_PATH = os.path.join(HERE, "upstream_tools.json")
DUMP = os.path.join(HERE, "upstream_live_dump.json")

sys.path.insert(0, os.path.join(ROOT, "python", "src"))

from roblox_studio_mcp.upstream import build_snapshot  # noqa: E402


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()


async def capture() -> Dict[str, Any]:
    """Connect through the Studio MCP proxy and capture ``tools/list``.

    Read-only by construction: the handshake and one ``tools/list``. No tool
    call touches the DataModel, so this cannot reach a place file or a team
    create. ``singleton=False`` so the connection is this process's own and is
    closed again (the process-wide singleton would outlive the run).
    """
    from roblox_studio_mcp.client import MCPClient
    from roblox_studio_mcp.roblox import default_args, default_command, default_shell

    client = MCPClient(default_command(), default_args(), shell=default_shell())
    try:
        await client.connect()
        result = await client.request("tools/list", {})
        return {
            "captured": _today(),
            "source": (
                "live tools/list through the StudioMCP proxy "
                "(%s)" % " ".join([default_command()] + default_args())
            ),
            "server_info": dict(client.server_info),
            "tools": list(result.get("tools") or []),
        }
    finally:
        await client.close()


def write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def snapshot_from(dump_path: str) -> Dict[str, Any]:
    with open(dump_path, encoding="utf-8") as handle:
        return build_snapshot(json.load(handle))


def main(argv: List[str] = None) -> int:  # noqa: ANN401 - argparse passthrough
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--live",
        action="store_true",
        help="connect through the Studio MCP proxy and capture a fresh dump",
    )
    target.add_argument(
        "--from",
        dest="from_path",
        default=None,
        metavar="DUMP",
        help="rebuild the snapshot from an existing dump file "
        "(default: the checked-in contract/upstream_live_dump.json)",
    )
    parser.add_argument(
        "--dump",
        default=DUMP,
        help="where a live capture is written "
        "(default: contract/upstream_live_dump.json)",
    )
    args = parser.parse_args(argv)

    if args.live:
        dump = asyncio.run(capture())
        write_json(dump_path, dump)
        print(
            "captured %d tools from the live proxy -> %s"
            % (len(dump.get("tools") or []), os.path.relpath(dump_path, ROOT))
        )
        snapshot = build_snapshot(dump)
    else:
        dump_path = args.from_path or DUMP
        if not os.path.exists(dump_path):
            print(
                "no dump at %s: run `python contract/build_upstream.py --live` "
                "with a Studio attached, or pass --from PATH" % dump_path,
                file=sys.stderr,
            )
            return 2
        with open(dump_path, encoding="utf-8") as handle:
            snapshot = build_snapshot(json.load(handle))
        print("rebuilt from %s" % os.path.relpath(dump_path, ROOT))

    write_json(SNAPSHOT, snapshot)
    print(
        "wrote %s: %d tools (trust: %s)"
        % (os.path.relpath(SNAPSHOT, ROOT), snapshot["tool_count"], snapshot["trust"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
