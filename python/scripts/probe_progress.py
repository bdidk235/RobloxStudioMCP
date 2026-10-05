"""Does Studio send anything at all while a tool call is in flight?

`_dispatch` drops every notification on the floor, so progress reporting, if it
exists, is invisible. This records every inbound message during a deliberately
slow call, to find out whether `notifications/progress` is ever sent.

Runs against the StartServer Studio, which has a live Server DataModel, so a
`task.wait` there blocks the call for real.
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from roblox_studio_mcp.client import MCPClient

SEEN = []
# Set before connect(), because the spy fires during the handshake, which
# happens before main() gets a chance to assign it.
START = time.monotonic()


def instrument(client: MCPClient) -> None:
    original = client._dispatch

    async def spy(message):
        SEEN.append(
            {
                "t": round(time.monotonic() - START, 3),
                "id": "id" in message,
                "method": message.get("method"),
                "keys": sorted(message.keys()),
            }
        )
        return await original(message)

    client._dispatch = spy


async def main():
    global START
    sid = os.environ["ROBLOX_STUDIO_ID"]
    from roblox_studio_mcp.client import DEFAULT_TIMEOUT
    print(f"client DEFAULT_TIMEOUT = {DEFAULT_TIMEOUT}")
    client = MCPClient(
        sys.executable, ["-m", "roblox_studio_mcp.extended_server"],
        env={**os.environ, "ROBLOX_STUDIO_ID": sid},
    )
    instrument(client)
    await client.connect()
    try:
        # baseline: what arrives during the handshake and tools/list
        START = time.monotonic()
        await client.list_tools()
        print(f"\nhandshake + tools/list: {len(SEEN)} messages")
        for m in SEEN:
            print(f"  t={m['t']:>6}  id={m['id']!s:<5} method={m['method']}")

        # now a deliberately slow call
        SEEN.clear()
        START = time.monotonic()
        print("\n--- slow call: task.wait(15) in the Server DataModel ---")
        t0 = time.monotonic()
        res = await client.call_tool(
            "execute_luau",
            {"code": 'task.wait(15)\nreturn "waited"', "datamodel_type": "Server"},
        )
        elapsed = time.monotonic() - t0
        print(f"returned after {elapsed:.1f}s")
        print(f"messages received during the wait: {len(SEEN)}")
        for m in SEEN:
            print(f"  t={m['t']:>6}  id={m['id']!s:<5} method={m['method']}  keys={m['keys']}")
        methods = {m["method"] for m in SEEN if m["method"]}
        if "notifications/progress" in methods:
            print("\nRESULT: progress notifications ARE sent")
        elif any(m["method"] for m in SEEN):
            print(f"\nRESULT: notifications sent but no progress: {sorted(methods)}")
        else:
            print("\nRESULT: silence. The server sends nothing while a call is in flight,")
            print("        so progress-aware waiting would gain nothing.")
    finally:
        await client.close()


asyncio.run(main())
