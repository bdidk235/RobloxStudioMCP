"""Wait until a Roblox Studio instance appears over MCP, then exit.

Polls ``list_studios`` until at least one Studio is attached (or a timeout
expires). Used by CI before running the live integration suite — see
``.github/workflows/ci.yml`` — and handy locally when Studio is still
starting::

    python -m examples.wait_for_studio [timeout_seconds]

Exits 0 when a Studio resolves, 1 on timeout. The Studio needs a place open
and the MCP server enabled (Assistant -> Manage MCP Servers).
"""

from __future__ import annotations

import asyncio
import sys
import time

from roblox_studio_mcp import RobloxStudio

POLL_INTERVAL = 5.0


async def main(timeout: float) -> int:
    deadline = time.monotonic() + max(0.0, timeout)
    start = time.monotonic()
    logged = 0.0
    async with await RobloxStudio.connect(singleton=False) as studio:
        while True:
            try:
                studios = await studio.list_studios()
            except Exception as exc:  # proxy warming up; keep polling
                print(f"waiting for Studio MCP proxy... ({exc})", flush=True)
                studios = []
            if studios:
                print(f"Studio ready: {studios[0]}", flush=True)
                return 0
            if time.monotonic() >= deadline:
                print(
                    "Timed out waiting for Studio. Open Studio with a place "
                    "loaded and enable the MCP server (Assistant -> Manage "
                    "MCP Servers), then retry.",
                    file=sys.stderr,
                )
                return 1
            elapsed = time.monotonic() - start
            if elapsed - logged >= 60:
                logged = elapsed
                print(f"still waiting for Studio... ({int(elapsed)}s elapsed)", flush=True)
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    sys.exit(asyncio.run(main(seconds)))
