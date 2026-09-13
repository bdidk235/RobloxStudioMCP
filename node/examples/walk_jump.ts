/**
 * Start play mode, make the character walk forward and jump, then leave.
 *
 * Requires Studio to be running with a place open and the MCP server enabled.
 * The character is driven by simulating keyboard input inside the running game
 * (`user_keyboard_input`), which targets the `Client` datamodel available only
 * while play mode is active.
 *
 * Run with: npx tsx examples/walk_jump.ts
 */

import { RobloxStudio } from "../src/index.js";

async function main(): Promise<void> {
  const studio = await RobloxStudio.connect();
  try {
    console.log(`Targeting studio: ${await studio.resolveStudioId()}`);

    // 1. Start playing.
    await studio.call("start_stop_play", { is_start: true });

    // 2. Walk forward: hold W for a second, then release.
    await studio.call("user_keyboard_input", {
      datamodel_type: "Client",
      actions: [
        { action: "keyDown", key_code: "W" },
        { action: "wait", wait_time_ms: 1000 },
        { action: "keyUp", key_code: "W" },
      ],
    });

    // 3. Jump: tap Space.
    await studio.call("user_keyboard_input", {
      datamodel_type: "Client",
      actions: [{ action: "keyPress", key_code: "Space" }],
    });

    // 4. Leave play mode (back to edit).
    await studio.call("start_stop_play", { is_start: false });

    console.log("Done: played, walked, jumped, and left play mode.");
  } finally {
    await studio.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
