/**
 * Use a single shared connection to one Studio instance, with tools disabled.
 *
 * Demonstrates the two package-level controls:
 *   * getSingleton()  -> one directed connection reused across calls
 *   * disabledTools   -> hide / refuse specific MCP tools
 */

import { closeSingleton, getSingleton } from "../src/index.js";

async function main(): Promise<void> {
  // Pin to one Studio instance (pass the id, or omit to auto-resolve).
  // Disable the heavy asset-generation tools so they aren't exposed.
  const studio = await getSingleton(null, {
    disabledTools: new Set(["generate_mesh", "segment_mesh", "generate_material"]),
  });
  try {
    // Every call reuses the same connection (no fresh proxy per call).
    const tools = await studio.listTools();
    console.log("Available tools:", tools.map((t) => t.name));

    const result = await studio.executeLuau("return 2 + 3");
    console.log("2 + 3 =", result.text().trim());
  } finally {
    await closeSingleton();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
