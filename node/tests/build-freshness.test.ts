/**
 * The build output must not be older than the source.
 *
 * Why this exists: `package.json` has `"main": "./dist/index.js"`, and during
 * one session `dist/` was found holding **30 built files older than their
 * sources** - including everything newly ported. The failure shape is the worst
 * kind, because it is invisible: the server starts, answers, and is simply not
 * the code you wrote. Switching the MCP config to Node would have run code from
 * before the port with no error anywhere.
 *
 * So staleness is a test, not a thing to remember. Run `npm run build` and this
 * goes green.
 */
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const SRC = join(ROOT, "src");
const DIST = join(ROOT, "dist");

/** Newest mtime under `dir`, or 0 when the directory is absent. */
function newestMtime(dir: string, exts: string[]): number {
  if (!existsSync(dir)) return 0;
  let newest = 0;
  const walk = (current: string): void => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (exts.includes(extname(entry.name))) {
        newest = Math.max(newest, statSync(full).mtimeMs);
      }
    }
  };
  walk(dir);
  return newest;
}

describe("build output is current", () => {
  it("dist exists - the Node server has been built at least once", () => {
    expect(
      existsSync(DIST),
      "node/dist is missing. Run `npm run build` in node/ before using the Node server.",
    ).toBe(true);
  });

  it("no built file is older than the newest source", () => {
    const newestSource = newestMtime(SRC, [".ts"]);
    expect(newestSource).toBeGreaterThan(0);

    const stale: string[] = [];
    const walk = (current: string): void => {
      for (const entry of readdirSync(current, { withFileTypes: true })) {
        const full = join(current, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if ([".js", ".d.ts"].includes(extname(entry.name))) {
          if (statSync(full).mtimeMs < newestSource) {
            stale.push(full.slice(ROOT.length + 1));
          }
        }
      }
    };
    walk(DIST);

    expect(
      stale,
      `${stale.length} built file(s) are older than src/. Anything run from ` +
        `dist/ is running old code. Run: npm run build\n  ` +
        stale.slice(0, 10).join("\n  "),
    ).toEqual([]);
  });

  it("the entry point named by package.json exists", () => {
    // `main` points into dist/, so a missing file fails at startup with a bare
    // module-not-found rather than anything that says which build is missing.
    const pkg = JSON.parse(
      require("node:fs").readFileSync(join(ROOT, "package.json"), "utf8"),
    ) as { main?: string };
    expect(pkg.main, "package.json has no main").toBeTruthy();
    expect(
      existsSync(join(ROOT, pkg.main as string)),
      `package.json main is ${pkg.main}, which does not exist. Run: npm run build`,
    ).toBe(true);
  });
});
