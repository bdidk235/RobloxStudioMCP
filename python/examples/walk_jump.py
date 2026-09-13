"""Start play mode, make the character walk forward and jump, then leave.

Requires Studio to be running with a place open and the MCP server enabled.
The character is driven by simulating keyboard input inside the running game
(``user_keyboard_input``), which targets the ``Client`` datamodel available only
while play mode is active.
"""

import asyncio

from roblox_studio_mcp import RobloxStudio


async def main() -> None:
    async with await RobloxStudio.connect() as studio:
        print(f"Targeting studio: {await studio.resolve_studio_id()}")

        # 1. Start playing.
        await studio.call("start_stop_play", {"is_start": True})

        # 2. Walk forward: hold W for a second, then release.
        await studio.call(
            "user_keyboard_input",
            {
                "datamodel_type": "Client",
                "actions": [
                    {"action": "keyDown", "key_code": "W"},
                    {"action": "wait", "wait_time_ms": 1000},
                    {"action": "keyUp", "key_code": "W"},
                ],
            },
        )

        # 3. Jump: tap Space.
        await studio.call(
            "user_keyboard_input",
            {
                "datamodel_type": "Client",
                "actions": [
                    {"action": "keyPress", "key_code": "Space"},
                ],
            },
        )

        # 4. Leave play mode (back to edit).
        await studio.call("start_stop_play", {"is_start": False})

        print("Done: played, walked, jumped, and left play mode.")


if __name__ == "__main__":
    asyncio.run(main())
