"""Extended Roblox Studio MCP — write/update script wrappers.

This subpackage exposes:
  * write_script  (full-body replace with status return)
  * update_script  (batch edits with graceful skipping + warnings)
  * execute_luau_from_file (run Luau source from a local file)

Both work against a running Studio instance via :class:`RobloxStudio`.

Programmatic use (recommended):

    import asyncio
    from roblox_studio_mcp.extended import RobloxStudio, write_script

    async def main():
        async with await RobloxStudio.connect() as studio:
            status = await write_script(
                studio,
                "game.ServerScriptService.MyScript",
                "print('hello')",
                create_if_missing=True,
            )
            # status is one of: "wrote", "unchanged", "created"

    asyncio.run(main())
"""

from .writer import write_script
from .updater import update_script, UpdateResult
from .extensions import execute_luau_from_file
from .registry import (
	list_instances as list_studio_instances,
	read_in_band_identity,
	record as record_instance,
	resolve as resolve_instance,
	registry_path,
)
from .capture import capture_png, capture_rgba, encode_png
from .breakpoints import (
    set_breakpoint,
    remove_breakpoint,
    clear_breakpoints,
    list_breakpoints,
)
from ..roblox import RobloxStudio

__all__ = [
	"RobloxStudio",
	"write_script",
	"update_script",
	"UpdateResult",
	"execute_luau_from_file",
	"list_studio_instances",
	"read_in_band_identity",
	"record_instance",
	"resolve_instance",
	"registry_path",
	"capture_png",
	"capture_rgba",
	"encode_png",
	"set_breakpoint",
	"remove_breakpoint",
	"clear_breakpoints",
	"list_breakpoints",
]
