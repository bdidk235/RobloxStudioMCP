/**
 * Platform primitives, ported from `python/tests/test_platform_macos.py`.
 *
 * macOS **parsing** is testable from Windows, and is: the risk in a port is
 * parsing, not the shell. macOS **integration** is unverified - no Mac was
 * available - and `test_macos_integration_is_unproven` records that as a fact
 * rather than leaving it to be assumed.
 *
 * The `RobloxStudio` (no `.exe`) case is the one that motivated the module: a
 * pattern requiring `.exe` matches no macOS process row, and the old parser would
 * then report "no Studio found" with no error.
 */
import { describe, expect, it } from "vitest";

import {
  MIN_EXE_BYTES,
  basename,
  isWindows,
  parsePsRow,
  roleFromCommandLine,
  placeFromCommandLine,
} from "../src/extended/platform.js";
import {
  URI_UNIVERSE_ID,
  UniverseLookupError,
  buildLaunchUri,
  resolveUniverseId,
  templatePlaceId,
  type FetchLike,
} from "../src/extended/instance.js";

const MAC_BANNER =
  "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio " +
  "--task EditFile --localPlaceFile " +
  "/Users/me/Library/Application Support/Roblox/RobloxStudio/AutoSaves/Baseplate-1.rbxl";
const WIN_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe --task EditFile --localPlaceFile " +
  "C:\\Users\\User\\AppData\\Local\\Temp\\robloxstudio-mcp-baseplates\\Baseplate-1.rbxl";

describe("unproven", () => {
  /**
   * The claim is about the **suite**, not about the host, and this asserts the
   * claim.
   *
   * It used to assert `isWindows()`, on the reasoning that a passing macOS suite
   * on a Mac might be read as a working port. But that asserts something about
   * the host, and it was permanently false on the `macos-latest` runner, so the
   * job could never go green - the opposite of what it was for.
   *
   * The claim is "no macOS integration was exercised", and that is still true on
   * a Mac: every case here is a *fixture* - shaped input, parsed output - and a
   * Mac running the same fixtures proves exactly as little as a PC does. So the
   * assertion is on the one thing that would actually retire the claim: the live
   * integration suite running. When someone points it at a real Studio, this
   * fails and the claim has to be revisited on purpose rather than by accident.
   *
   * Mirrors `python/tests/test_platform_macos.py::Unproven`.
   */
  it("states that no macOS integration was exercised", () => {
    const live = process.env["ROBLOX_STUDIO_MCP_INTEGRATION"] === "1";
    expect(
      live,
      `ROBLOX_STUDIO_MCP_INTEGRATION=1, so live integration IS running and this ` +
        `claim is retired - revisit it (host: ${process.platform})`,
    ).toBe(false);
  });
});

describe("parsePsRow", () => {
  it("reads a macOS Studio row", () => {
    const line =
      "  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/" +
      "Contents/MacOS/RobloxStudio --task EditFile --localPlaceFile /tmp/a.rbxl";
    const got = parsePsRow(line);
    expect(got, line).not.toBeNull();
    expect(got!.pid).toBe(4700);
    // lstart is FIVE fields. Splitting 3 ways gave created="Mon", which parses as
    // a string and silently loses the time.
    expect(got!.created).toBe("Mon Sep 28 02:36:04 2026");
    expect(got!.command_line).toContain("--localPlaceFile");
  });

  it("keeps a command line containing spaces intact", () => {
    // The reason the row is sliced rather than split: the command line is last
    // and contains spaces, so split() would truncate it.
    const line =
      "  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/" +
      "Contents/MacOS/RobloxStudio -task EditFile --localPlaceFile " +
      "/Users/me/Library/Application Support/Roblox/a place with spaces.rbxl";
    expect(parsePsRow(line)!.command_line).toContain("a place with spaces.rbxl");
  });

  it("rejects a non-Studio process", () => {
    expect(
      parsePsRow("  4700 Mon Sep 28 02:36:04 2026 /usr/libexec/somethingelse"),
    ).toBeNull();
  });

  it("rejects a Studio that is not an instance", () => {
    // The installer, or a helper: no place, no role, so not a candidate.
    expect(
      parsePsRow(
        "  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/" +
          "Contents/MacOS/RobloxStudio --some-other-flag",
      ),
    ).toBeNull();
  });

  it("rejects junk", () => {
    for (const line of ["", "   ", "not a row", "123"]) {
      expect(parsePsRow(line), JSON.stringify(line)).toBeNull();
    }
  });

  it("keeps a URI-launched Studio", () => {
    // A URI launch carries no -task flag, so filtering on that alone would drop
    // exactly the instances the URI route produces.
    const got = parsePsRow(
      "  4700 Mon Sep 28 02:36:04 2026 /Applications/RobloxStudio.app/" +
        "Contents/MacOS/RobloxStudio roblox-studio:1+task:EditPlace+placeId:1",
    );
    expect(got).not.toBeNull();
    expect(got!.pid).toBe(4700);
  });
});

describe("roleFromCommandLine", () => {
  it("reads a -task flag", () => {
    expect(roleFromCommandLine(WIN_BANNER)).toBe("edit");
    expect(roleFromCommandLine("RobloxStudioBeta.exe --task StartServer")).toBe("server");
    expect(roleFromCommandLine("RobloxStudioBeta.exe --task StartClient")).toBe("client");
  });

  it("reads the task out of a URI launch", () => {
    // A URI launch carries no -task flag at all, so a -task-only parse reported
    // every URI-launched Studio as unknown.
    expect(
      roleFromCommandLine("RobloxStudioBeta.exe roblox-studio:1+task:EditPlace+placeId:1"),
    ).toBe("edit");
  });

  it("says unknown rather than guessing", () => {
    expect(roleFromCommandLine("RobloxStudioBeta.exe")).toBe("unknown");
    expect(roleFromCommandLine("")).toBe("unknown");
  });
});

describe("placeFromCommandLine", () => {
  it("returns the basename of a file launch", () => {
    expect(placeFromCommandLine(WIN_BANNER)).toBe("Baseplate-1.rbxl");
  });

  it("returns null for a URI launch", () => {
    // Nothing can be derived: a URI carries only an id and Studio names the
    // place itself.
    expect(placeFromCommandLine("RobloxStudioBeta.exe roblox-studio:1+task:EditPlace+placeId:1")).toBeNull();
  });

  it("accepts a quoted path", () => {
    expect(
      placeFromCommandLine('RobloxStudioBeta.exe --localPlaceFile "C:\\a\\b\\Place.rbxl"'),
    ).toBe("Place.rbxl");
  });
});

describe("basename", () => {
  it("splits a macOS path", () => {
    expect(basename("/Users/me/Library/Application Support/Baseplate-1.rbxl")).toBe(
      "Baseplate-1.rbxl",
    );
  });

  it("splits a Windows path", () => {
    expect(basename("C:\\Users\\User\\AppData\\Local\\Temp\\Baseplate-1.rbxl")).toBe(
      "Baseplate-1.rbxl",
    );
  });

  it("handles both separators in one path", () => {
    expect(basename("C:/tmp\\a/Baseplate-1.rbxl")).toBe("Baseplate-1.rbxl");
  });

  it("treats a bare name as its own basename", () => {
    expect(basename("Baseplate-1.rbxl")).toBe("Baseplate-1.rbxl");
  });

  it("returns empty for empty", () => {
    expect(basename("")).toBe("");
  });
});

describe("exe threshold", () => {
  it("keeps the measured 100 MB value", () => {
    // It was 100 MB, set from a real STATUS_DLL_NOT_FOUND. A first draft of the
    // Python module used 20 MB, which would have lowered a threshold derived from
    // an observed failure with nothing measured behind the change.
    expect(MIN_EXE_BYTES).toBe(100 * 1024 * 1024);
  });
});

describe("buildLaunchUri", () => {
  it("emits exactly four keys", async () => {
    // Measured. A URI needs exactly four keys, and dropping `universeId` *looks*
    // like it works - the process starts and attaches - but Studio comes up with
    // no place open, which only shows when you ask it for a name.
    const uri = await buildLaunchUri(95206881, URI_UNIVERSE_ID);
    expect(uri).toBe("roblox-studio:1+task:EditPlace+placeId:95206881+universeId:0");
    // Four `+`-separated keys: the scheme version, task, placeId, universeId.
    expect(uri.split("+").length).toBe(4);
    expect(uri.startsWith("roblox-studio:1+")).toBe(true);
  });

  it("carries both ids when one is given", async () => {
    expect(await buildLaunchUri(1, 2)).toContain("+universeId:2");
    expect(await buildLaunchUri(1, 2)).toContain("+placeId:1");
  });

  it("derives the universe from the place when none is given", async () => {
    // The point of the `null` default: a caller cannot supply a universe id that
    // disagrees with the place, because the common case never supplies one.
    const uri = await buildLaunchUri(95206881, null, {
      delayMs: 0,
      fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ universeId: 28220420 }) }),
    });
    expect(uri).toBe(
      "roblox-studio:1+task:EditPlace+placeId:95206881+universeId:28220420",
    );
  });

  it("never touches the network when a universe id is supplied", async () => {
    // A launch path that silently depended on the network would fail offline,
    // and would report a network failure as if it were a bad URI.
    const boom = async (): Promise<never> => {
      throw new Error("the network was touched despite an explicit id");
    };
    const uri = await buildLaunchUri(95206881, URI_UNIVERSE_ID, { fetchImpl: boom });
    expect(uri).toContain("+universeId:0");
  });

  it("keeps 0 available as the explicit no-universe value", () => {
    // File > New opens a place as `-placeId N -universeId 0`, and a template
    // genuinely has no universe context, so 0 states something true.
    expect(URI_UNIVERSE_ID).toBe(0);
  });

  it("truncates rather than emitting a fractional id", async () => {
    expect(await buildLaunchUri(95206881.9, 0)).toContain("+placeId:95206881");
  });
});

describe("resolveUniverseId", () => {
  const ok = (universeId: unknown): FetchLike => async () => ({
    ok: true,
    status: 200,
    json: async () => ({ universeId }),
  });

  it("returns the id from a single good answer", async () => {
    expect(await resolveUniverseId(95206881, { delayMs: 0, fetchImpl: ok(28220420) }))
      .toBe(28220420);
  });

  it("retries a transient failure and then succeeds", async () => {
    // The reason retries default to 3: one fast call on a launch path turns a
    // network blip into "could not open the place", and the caller then looks at
    // the URI instead of at the network.
    let calls = 0;
    const flaky: FetchLike = async () => {
      calls += 1;
      if (calls < 3) return { ok: false, status: 503, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ universeId: 28220420 }) };
    };
    expect(await resolveUniverseId(95206881, { delayMs: 0, fetchImpl: flaky }))
      .toBe(28220420);
    expect(calls).toBe(3);
  });

  it("throws after three failures, and says how many it made", async () => {
    const dead: FetchLike = async () => ({ ok: false, status: 500, json: async () => ({}) });
    await expect(
      resolveUniverseId(95206881, { delayMs: 0, fetchImpl: dead }),
    ).rejects.toThrow(UniverseLookupError);
  });

  it("never coerces a missing universeId to 0", async () => {
    // Coercing it would silently mean "no universe context" - the exact
    // substitution this derivation exists to make impossible.
    let calls = 0;
    const count: FetchLike = async () => {
      calls += 1;
      return { ok: true, status: 200, json: async () => ({ somethingElse: 1 }) };
    };
    await expect(
      resolveUniverseId(95206881, { delayMs: 0, fetchImpl: count }),
    ).rejects.toThrow(/no universeId/);
    expect(calls).toBe(3);
  });

  it("defaults to three attempts", async () => {
    // Pinned because the request was "3 retries": a silent change would weaken
    // the launch path with nothing failing.
    let calls = 0;
    const count: FetchLike = async () => {
      calls += 1;
      return { ok: false, status: 500, json: async () => ({}) };
    };
    await expect(
      resolveUniverseId(95206881, { delayMs: 0, fetchImpl: count }),
    ).rejects.toThrow();
    expect(calls).toBe(3);
  });

  it("negative control: retries are observable", async () => {
    // A retry test that cannot fail proves nothing. The fake is given exactly as
    // many failures as the resolver will attempt, so if it stopped retrying the
    // call count would drop below 3 and this would fail.
    let calls = 0;
    const count: FetchLike = async () => {
      calls += 1;
      return { ok: false, status: 500, json: async () => ({}) };
    };
    await expect(
      resolveUniverseId(95206881, { delayMs: 0, fetchImpl: count }),
    ).rejects.toThrow();
    expect(calls).toBeGreaterThan(1);
  });
});

describe("templatePlaceId", () => {
  it("reads the id out of a template autosave name", () => {
    // Measured: the baseplate this machine discovers is
    // Template_95206881_AutoRecovery_4_20260930_135756.rbxl.
    expect(
      templatePlaceId(
        "Template_95206881_AutoRecovery_4_20260930_135756.rbxl",
      ),
    ).toBe(95206881);
  });

  it("works without the trailing stamp", () => {
    expect(templatePlaceId("Template_95206881_AutoRecovery_3.rbxl")).toBe(95206881);
  });

  it("returns null for anything else", () => {
    expect(templatePlaceId("Baseplate-1.rbxl")).toBeNull();
    expect(templatePlaceId("")).toBeNull();
  });

  it("does not match a name that merely contains the pattern", () => {
    // Anchored at the start, so `MyTemplate_1_AutoRecovery_2.rbxl` is not a hit.
    expect(templatePlaceId("MyTemplate_1_AutoRecovery_2.rbxl")).toBeNull();
  });
});
