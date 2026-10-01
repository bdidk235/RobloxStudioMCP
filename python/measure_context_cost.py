"""Measure the always-on token cost of the tool list.

The tool schemas sit in the context of every single call, so their size is a
fixed tax. This measures what is actually served, not what the source looks
like, and splits the total into the part I control (extended) and the part I
do not (relayed from Studio).
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from roblox_studio_mcp.client import MCPClient


async def main():
    sid = os.environ.get("ROBLOX_STUDIO_ID", "").strip()
    client = MCPClient(
        sys.executable,
        ["-m", "roblox_studio_mcp.extended_server"],
        env={**os.environ, "ROBLOX_STUDIO_ID": sid} if sid else None,
    )
    await client.connect()
    try:
        result = await client.list_tools()
        tools = result if isinstance(result, list) else result.tools
        rows = []
        for t in tools:
            name = getattr(t, "name", "") or ""
            desc = getattr(t, "description", "") or ""
            schema = json.dumps(getattr(t, "input_schema", {}) or {}, separators=(",", ":"))
            rows.append(
                {
                    "name": name,
                    "ext": name.startswith("extended_"),
                    "desc": len(desc),
                    "schema": len(schema),
                    "total": len(name) + len(desc) + len(schema),
                }
            )
    finally:
        await client.close()

    ext = [r for r in rows if r["ext"]]
    rel = [r for r in rows if not r["ext"]]
    tot_ext = sum(r["total"] for r in ext)
    tot_rel = sum(r["total"] for r in rel)

    print(f"tools: {len(rows)}  ({len(rel)} relayed, {len(ext)} extended)")
    print(f"relayed total : {tot_rel:>7,} chars")
    print(f"extended total: {tot_ext:>7,} chars  ({tot_ext / (tot_rel + tot_ext) * 100:.1f}% of the list)")
    print()
    print("extended, largest first:")
    for r in sorted(ext, key=lambda r: -r["total"]):
        print(
            f"  {r['total']:>6,}  desc={r['desc']:>5,}  schema={r['schema']:>5,}  {r['name']}"
        )
    print()
    print("relayed, largest first (not mine, for reference):")
    for r in sorted(rel, key=lambda r: -r["total"])[:6]:
        print(
            f"  {r['total']:>6,}  desc={r['desc']:>5,}  schema={r['schema']:>5,}  {r['name']}"
        )
    print()
    top = max(ext, key=lambda r: r["total"]) if ext else None
    if top:
        print(f"single worst offender: {top['name']} at {top['total']:,} chars")
        print(f"its description is {top['desc'] / top['total'] * 100:.0f}% of its own entry")


asyncio.run(main())
