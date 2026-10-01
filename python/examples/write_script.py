"""Write-like wrapper around `multi_edit` for game-tree scripts.

See :func:`roblox_studio_mcp.extended.writer.write_script` for the full
documentation.  This module exists so the example can also be run as a script::

    python -m examples.write_script game.ServerScriptService.MyScript [studio_id]
"""

from __future__ import annotations

import asyncio
import sys

from roblox_studio_mcp.extended import RobloxStudio, write_script


if __name__ == "__main__":
    raw_args = sys.argv[1:]
    create = "--create" in raw_args
    positional = [a for a in raw_args if a != "--create"]
    if not positional:
        print(
            "Usage: python -m examples.write_script "
            "<game.ScriptContainer.Name> [studio_id] [--create]",
            file=sys.stderr,
        )
        sys.exit(1)

    target = positional[0]
    studio_id = positional[1] if len(positional) > 1 else None

    async def main():
        async with await RobloxStudio.connect(studio_id=studio_id) as studio:
            status = await write_script(
                studio,
                target,
                "print('Hello from write_script!')",
                create_if_missing=create,
            )
            if status == "created":
                print(f"Created {target}")
            elif status == "wrote":
                print(f"Updated {target}")
            else:
                print(f"Unchanged: {target} already has this content")

    asyncio.run(main())
