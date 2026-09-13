"""Run a snippet of Luau inside Studio and print the result."""

import asyncio

from roblox_studio_mcp import RobloxStudio


async def main() -> None:
    async with await RobloxStudio.connect() as studio:
        # Which Studio instance are we targeting?
        print(f"studio_id: {await studio.resolve_studio_id()}")

        result = await studio.execute_luau(
            "return { name = game.PlaceId, version = version() }"
        )
        print("Result:", result.text())


if __name__ == "__main__":
    asyncio.run(main())
