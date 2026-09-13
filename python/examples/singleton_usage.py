"""Use a single shared connection to one Studio instance, with tools disabled.

Demonstrates the two package-level controls:
  * get_singleton()  -> one directed connection reused across calls
  * disabled_tools=  -> hide / refuse specific MCP tools
"""

import asyncio

from roblox_studio_mcp import close_singleton, get_singleton


async def main() -> None:
    # Pin to one Studio instance (pass the id, or omit to auto-resolve).
    # Disable the heavy asset-generation tools so they aren't exposed.
    studio = await get_singleton(
        disabled_tools={"generate_mesh", "segment_mesh", "generate_material"},
    )

    # Every call reuses the same connection (no fresh proxy per call).
    tools = await studio.list_tools()
    print("Available tools:", [t.name for t in tools])

    result = await studio.execute_luau("return 2 + 3")
    print("2 + 3 =", result.text().strip())

    await close_singleton()


if __name__ == "__main__":
    asyncio.run(main())
