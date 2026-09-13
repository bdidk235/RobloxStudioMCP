/**
 * Live integration tests against a real Roblox Studio with MCP enabled.
 *
 * Skipped unless `ROBLOX_STUDIO_MCP_INTEGRATION=1` is set. Studio must be
 * open with a place loaded and the MCP server enabled (Assistant → Manage
 * MCP Servers). In CI this is handled by the `node-studio` job — see
 * `.github/workflows/ci.yml` — which installs Studio, waits for it with
 * `examples/wait_for_studio.ts`, then runs this suite.
 *
 * The probe script uses a fixed DataModel path so reruns are idempotent
 * (`writeLikeMultiEdit` just reports `"unchanged"`).
 */

import { describe, expect, it } from "vitest";
import { RobloxStudio } from "../src/index.js";
import { stripLinePrefixes, writeLikeMultiEdit } from "../src/extended/index.js";

const suite =
  process.env["ROBLOX_STUDIO_MCP_INTEGRATION"] === "1" ? describe : describe.skip;

const PROBE_PATH = "game.ServerScriptService.MCPIntegrationProbe";
const PROBE_SOURCE = "print('MCP integration probe')\n";

suite("studio integration", () => {
  it(
    "resolves, executes, writes and reads",
    { timeout: 120_000 },
    async () => {
      const studio = await RobloxStudio.connect({ singleton: false });
      try {
        const sid = await studio.resolveStudioId();
        expect(sid.length).toBeGreaterThan(0);

        const names = (await studio.listTools()).map((t) => t.name);
        expect(names).toContain("execute_luau");

        const result = await studio.executeLuau("return 6 * 7");
        expect(result.text().trim()).toBe("42");

        const status = await writeLikeMultiEdit(studio, PROBE_PATH, PROBE_SOURCE, {
          createIfMissing: true,
        });
        expect(["wrote", "created", "unchanged"]).toContain(status);

        const readBack = await studio.scriptRead(PROBE_PATH);
        expect(stripLinePrefixes(readBack.text())).toBe(PROBE_SOURCE);

        const state = await studio.getStudioState();
        expect(state.text().length).toBeGreaterThan(0);
      } finally {
        await studio.close();
      }
    },
  );
});
