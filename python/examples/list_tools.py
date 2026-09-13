"""List every tool exposed by the Roblox Studio MCP server."""

import asyncio

from roblox_studio_mcp import RobloxStudio


async def main() -> None:
    async with await RobloxStudio.connect() as studio:
        print(f"Server: {studio.client.server_info.get('name', '?')}")
        tools = await studio.list_tools()
        for tool in tools:
            required = ", ".join(tool.required) or "(none)"
            print(f"  {tool.name}  [required: {required}]")


if __name__ == "__main__":
    asyncio.run(main())
