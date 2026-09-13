"""Extended Roblox Studio MCP — write/update-like wrappers.

This subpackage exposes:
  * write_like_multi_edit  (full-body replace with status return)
  * update_like_multi_edit  (batch edits with graceful skipping + warnings)
  * execute_luau_from_file (run Luau source from a local file)

Both work against a running Studio instance via :class:`RobloxStudio`.

Programmatic use (recommended):

    import asyncio
    from roblox_studio_mcp.extended import RobloxStudio, write_like_multi_edit

    async def main():
        async with await RobloxStudio.connect() as studio:
            status = await write_like_multi_edit(
                studio,
                "game.ServerScriptService.MyScript",
                "print('hello')",
                create_if_missing=True,
            )
            # status is one of: "wrote", "unchanged", "created"

    asyncio.run(main())
"""

from .writer import write_like_multi_edit
from .updater import update_like_multi_edit, UpdateResult
from .extensions import execute_luau_from_file
from ..roblox import RobloxStudio

__all__ = [
	"RobloxStudio",
	"write_like_multi_edit",
	"update_like_multi_edit",
	"UpdateResult",
	"execute_luau_from_file",
]
