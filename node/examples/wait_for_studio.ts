/**
 * Wait until a Roblox Studio instance appears over MCP, then exit.
 *
 * Polls `listStudios` until at least one Studio is attached (or a timeout
 * expires). Used by CI before running the live integration suite — see
 * `.github/workflows/ci.yml` — and handy locally when Studio is still
 * starting.
 *
 * Run with: npx tsx examples/wait_for_studio.ts [timeoutSeconds]
 *
 * Exits 0 when a Studio resolves, 1 on timeout. The Studio needs a place
 * open and the MCP server enabled (Assistant → Manage MCP Servers).
 */

import { RobloxStudio } from "../src/index.js";

const POLL_INTERVAL_MS = 5_000;

async function main(): Promise<void> {
  const raw = Number(process.argv[2] ?? 600);
  const timeoutSeconds = Number.isFinite(raw) && raw >= 0 ? raw : 600;
  const deadline = Date.now() + Math.max(0, timeoutSeconds) * 1000;
  const studio = await RobloxStudio.connect({ singleton: false });
  try {
    for (;;) {
      try {
        const studios = await studio.listStudios();
        if (studios.length > 0) {
          console.log(`Studio ready: ${JSON.stringify(studios[0])}`);
          return;
        }
      } catch (err) {
        // Proxy warming up; keep polling.
        console.log(`waiting for Studio MCP proxy... (${String(err)})`);
      }
      if (Date.now() >= deadline) {
        console.error(
          "Timed out waiting for Studio. Open Studio with a place loaded and enable " +
            "the MCP server (Assistant → Manage MCP Servers), then retry.",
        );
        process.exitCode = 1;
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
  } finally {
    await studio.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
