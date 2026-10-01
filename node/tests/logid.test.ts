/**
 * Reading a Studio process's identity out of its own log.
 *
 * Ported from five Python suites, all of whose cases are reproduced here:
 *
 * - `python/tests/test_logid.py` - pid, command line, guids, name join, window
 * - `python/tests/test_logid_formats.py` - FLog line-format recognition, and the PID
 *   across all four formats
 * - `python/tests/test_logid_outcome.py` - the place-open outcome parser
 * - `python/tests/test_logid_scaling.py` - the prefix read and its fallback
 * - `python/tests/test_session_guid.py` - the AutoRecovery counter
 * - `python/tests/test_parent_edge.py` - the `-parentPid` edge, minus its
 *   `instance.resolve_pid_for_studio` wiring class, which exercises a resolver this
 *   port does not cover
 *
 * The strings below are the real shapes, taken from logs on the measuring machine.
 * The point of each test is the *trap* it locks down, not the happy path: every one
 * of these is a way the parse can silently produce a plausible wrong answer.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DATA_CONTINUATION,
  DATA_FORMAT_TYPE1,
  DATA_FORMAT_TYPE2,
  DATA_FORMAT_TYPE3,
  DATA_FORMAT_TYPE4,
  DATA_FORMATS,
  DATA_PREAMBLE,
  DATA_UNRECOGNISED,
  PREFIX_BYTES,
  ancestorChain,
  ambiguousReason,
  classifyLine,
  formatReport,
  isPlaytestTask,
  liveIdentities,
  matchMeshName,
  meshNamesForIdentity,
  nameMatchesIdentity,
  newLogIdentity,
  noPidReason,
  openOutcome,
  parseIdentity,
  parseProcessStarted,
  placeSessionPath,
  playtestChildren,
  readIdentity,
  readOpenOutcome,
  readPlaceSessionPath,
  resolveUnnamedStudio,
  sessionGuids,
  setLogDirectory,
  setPrefixBytes,
  stampSeconds,
  type LogIdentity,
} from "../src/extended/logid.js";

/**
 * A full identity with the given fields overridden.
 *
 * The Python tests build partial dicts directly, which is the thing this port
 * cannot do: `LogIdentity` names every field, so a test that only cares about
 * `place_path` still has to say what the other eleven are. `newLogIdentity` is that
 * "absent", and the override is the assertion.
 */
const identity = (over: Partial<LogIdentity> = {}): LogIdentity => ({
  ...newLogIdentity(),
  ...over,
});

// --------------------------------------------------------------------------- //
// Fixtures
// --------------------------------------------------------------------------- //

/**
 * Wrap a body the way a real log frames it, so the banner regex is exercised
 * against the surrounding noise rather than against a clean string.
 */
const _log = (body: string): string =>
  "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] " +
  "All use of Roblox services must comply with Roblox's Terms of Use\n" +
  body +
  "\n" +
  "2026-09-30T09:12:05.831Z,0.831000,0edc,6,Info [FLog::Output] trailing line\n";

const FILE_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe --task EditFile --localPlaceFile " +
  "C:\\Users\\User\\AppData\\Local\\Temp\\robloxstudio-mcp-baseplates\\Baseplate-555883251.rbxl";

const URI_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe roblox-studio:1+task:EditPlace+placeId:95206881+universeId:28220420";

const SERVER_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe -placeVersion 0 -creatorId 0 -task StartServer " +
  "-localProjectFile C:/Users/User/AppData/Local/Temp/robloxstudio-mcp-baseplates/Baseplate-1340472086.rbxl";

const CLIENT_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe -task StartClient -rbxTransportToken bG9jYWxfdGVzdA== " +
  "-localProjectFile C:/Users/User/AppData/Local/Temp/robloxstudio-mcp-baseplates/Baseplate-1340472086.rbxl";

const LAUNCHER_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe -startEvent www.roblox.com/robloxQTStudioStartedEvent " +
  '-launchIntentString {"task":"None"} -parentPid 1234';

const PID_LINE =
  "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] " +
  "Constructing UIThreadNotifier for process '16240' " +
  "with id 'https://www.roblox.com-Studio'";

const GUID_LINES =
  "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] " +
  "Session GUID is 45105BED-F1B1-4847-B213-FB0DD03F7E1B\n" +
  "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] " +
  "Machine GUID is 3FDE6FD7-3243-49EF-B961-0E81894BA3F0";

/**
 * The type-4 prefix, verbatim from a real log. The PID line is the one this whole
 * module exists to find, and it is type 4 on every log measured here.
 */
const PID_LINE_T4 =
  "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] " +
  "Constructing UIThreadNotifier for process '16240' with id " +
  "'https://www.roblox.com-Studio'";

/**
 * The same record with the **severity field empty** - the variant that is 29.6% of
 * every real line measured and that the format spec's own example shows, while its
 * prose never mentions it. Pinned because dropping the optional severity field would
 * mark tens of thousands of real lines unrecognised.
 */
const PID_LINE_T4_NO_SEVERITY =
  "2026-09-30T09:16:54.175Z,1.175599,1a78,6 [FLog::UIThreadNotifier] " +
  "Constructing UIThreadNotifier for process '16240'";

/**
 * Type 1, from the spec's example. **Never observed on this machine**: zero of
 * 129,901 lines across 67 logs. The fixture is the spec's, so a pass here means "the
 * recogniser agrees with the spec", not "this format was seen".
 */
const PID_LINE_T1 =
  "1712972981.05664,7fb4,6 [FLog::UIThreadNotifier] " +
  "Constructing UIThreadNotifier for process '16240'";

/**
 * Type 1 with a severity word in place of the numeric third field, the spelling the
 * original brief used. Included because the spec's own type-1 example and its type-1
 * *heading* disagree about whether that field exists, so a recogniser that only
 * accepts the spec's example rejects a real-world spelling of it.
 */
const PID_LINE_T1_SEVERITY =
  "1712972981.05664,0edc,Warning [FLog::UIThreadNotifier] " +
  "Constructing UIThreadNotifier for process '16240'";

/**
 * Type 2: type 1's fields with **no channel marker**. The marker is the only thing
 * separating the two, which is why the two share a pattern and are told apart by
 * {@link classifyLine} rather than by their timestamps.
 */
const PID_LINE_T2 =
  "1712859371.37087,7b3c,6 Constructing UIThreadNotifier for process '16240'";

/** Type 3: TimeSinceStarted, a *named* thread, and no commas at all. */
const PID_LINE_T3 = "0.01454 7dbc: Constructing UIThreadNotifier for process '16240'";

const FORMATS_BANNER =
  "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35" +
  "\\RobloxStudioBeta.exe --task EditFile --localPlaceFile " +
  "C:\\Users\\User\\AppData\\Local\\Temp\\Baseplate-555883251.rbxl";

/**
 * A line format not in the table: the epoch is present, the field count is not one of
 * the four. This is the case the whole module exists to stop hiding.
 */
const GARBAGE = "1712972981.05664,7fb4 [FLog::Output] a line from a format we do not read";

const lines = (...parts: string[]): string => parts.join("\n") + "\n";

// --------------------------------------------------------------------------- //
// test_logid.py
// --------------------------------------------------------------------------- //

describe("the pid", () => {
  it("reads the pid", () => {
    expect(parseIdentity(_log(PID_LINE)).pid).toBe(16240);
  });

  it("is null when absent, not zero", () => {
    // Zero is a real pid shape on some systems, so absence must not look like a
    // found value; a caller would then join on a process that does not exist.
    expect(parseIdentity(_log(FILE_BANNER)).pid).toBeNull();
  });

  it("will not truncate a longer process name", () => {
    // The pattern ends at the closing quote. A name with digits after the pid must
    // not yield a short wrong pid.
    const line =
      "2026-09-30T09:16:54.175Z,1.1,1a78,6,Info [FLog::UIThreadNotifier] " +
      "Constructing UIThreadNotifier for process '1234567890'";
    expect(parseIdentity(_log(line)).pid).toBe(1234567890);
  });
});

describe("the command line", () => {
  it("yields the full path on the file route", () => {
    const got = parseIdentity(_log(FILE_BANNER));
    expect(got.place_path).toMatch(/Baseplate-555883251\.rbxl$/);
    expect(got.task).toBe("EditFile");
    expect(got.place_id).toBeNull();
  });

  it("yields the ids and not a path on the URI route", () => {
    // The URI route carries the ids inline and no path at all, so treating it as a
    // path launch would produce a match on nothing.
    const got = parseIdentity(_log(URI_BANNER));
    expect(got.place_id).toBe(95206881);
    expect(got.universe_id).toBe(28220420);
    expect(got.place_path).toBeNull();
    expect(got.task).toBe("EditPlace");
  });

  it("uses -localProjectFile for a play test", () => {
    // A play test's server and clients spell it `-localProjectFile` with one dash.
    // Matching only `--localPlaceFile` left 8 of 45 real logs with no place, which is
    // the whole play-test population.
    for (const banner of [SERVER_BANNER, CLIENT_BANNER]) {
      const got = parseIdentity(_log(banner));
      expect(
        got.place_path,
        `expected the project file, got ${JSON.stringify(got.place_path)}`,
      ).toMatch(/Baseplate-1340472086\.rbxl$/);
    }
  });

  it("reads the play-test roles, parent and transport token", () => {
    const server = parseIdentity(_log(SERVER_BANNER));
    const client = parseIdentity(_log(CLIENT_BANNER));
    expect(server.task).toBe("StartServer");
    expect(client.task).toBe("StartClient");
    expect(client.transport_token).toBe("bG9jYWxfdGVzdA==");
  });

  it("does not mistake a launcher's task:None for no task", () => {
    // `-launchIntentString {"task":"None"}` is the launcher saying it has no task,
    // which is different from the log saying nothing. Reporting "None" as the task
    // would make a launch look identified.
    const got = parseIdentity(_log(LAUNCHER_BANNER));
    expect(got.task).toBe("None");
    expect(got.parent_pid).toBe(1234);
    expect(got.place_path).toBeNull();
  });

  it("treats a bare executable as not a crash", () => {
    const got = parseIdentity(
      _log(
        "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-6b0e880a1a144428" +
          "\\RobloxStudioBeta.exe",
      ),
    );
    expect(got.place_path).toBeNull();
    expect(got.task).toBeNull();
  });

  it("does not mistake a timestamped line for a banner", () => {
    // Every ordinary log line is prefixed with a date, so the banner pattern must
    // not anchor loosely enough to match one and call it a command line. This is the
    // `(?!\d{4}-)` lookahead's only real job, and it is kept for that reason alone.
    const got = parseIdentity(
      _log(
        "2026-09-30T09:12:05.347Z,0.347599,0edc,6 [FLog::Output] " +
          "Creating PolicyContext(Root)",
      ),
    );
    expect(got.place_path).toBeNull();
    expect(got.task).toBeNull();
  });

  it("keeps a path containing a space intact", () => {
    // The macOS AutoSaves path is `.../Application Support/Roblox/...`, and the
    // space is a fixed component. A `(\S+)` capture stopped at it and produced
    // `/Users/me/Library/Application` - a path that exists nowhere, and a silent
    // failure, because the truncated value still yielded a basename and the name
    // join just never matched. Windows cannot produce this input, so it is pinned
    // here.
    const banner =
      "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio --task EditFile " +
      "--localPlaceFile /Users/me/Library/Application Support/Roblox/RobloxStudio/" +
      "AutoSaves/Template_1_AutoRecovery_3.rbxl";
    expect(parseIdentity(_log(banner)).place_path).toBe(
      "/Users/me/Library/Application Support/Roblox/RobloxStudio/AutoSaves/" +
        "Template_1_AutoRecovery_3.rbxl",
    );
  });

  it("reads a macOS banner, which has no .exe suffix", () => {
    // A pattern requiring `.exe` matches no macOS banner and reports "no identity"
    // with no error at all.
    const banner =
      "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio --task EditFile " +
      "--localPlaceFile /tmp/Baseplate-1.rbxl";
    const got = parseIdentity(_log(banner));
    expect(got.task).toBe("EditFile");
    expect(got.place_path).toBe("/tmp/Baseplate-1.rbxl");
  });

  it("reads the banner out of a CRLF log", () => {
    // Python read these files in text mode, which normalised CRLF to LF. Node reads
    // bytes, so the banner pattern has to survive a raw `\r` at the end of the line
    // on its own - `$` in JavaScript's multiline mode also matches before a `\r`.
    const text =
      PID_LINE + "\r\n" + FILE_BANNER + "\r\n";
    const got = parseIdentity(text);
    expect(got.pid).toBe(16240);
    expect(got.task).toBe("EditFile");
    expect(got.place_path).toMatch(/Baseplate-555883251\.rbxl$/);
  });

  it("stops a path at the next flag", () => {
    const banner =
      "C:\\R\\RobloxStudioBeta.exe --task EditFile --localPlaceFile C:\\tmp\\a.rbxl " +
      "-userid 1183256136 -parentPid 42";
    const got = parseIdentity(_log(banner));
    expect(got.place_path).toBe("C:\\tmp\\a.rbxl");
    expect(got.parent_pid).toBe(42);
  });

  it("takes a quoted path up to the closing quote", () => {
    const banner =
      'C:\\R\\RobloxStudioBeta.exe --localPlaceFile "C:\\tmp\\a place.rbxl" -task EditFile';
    expect(parseIdentity(_log(banner)).place_path).toBe("C:\\tmp\\a place.rbxl");
  });

  it("reads the separate -placeId / -universeId flags of a File > New child", () => {
    // `_URI_PLACE_RE` only matches the inline `+placeId:N` shape, so missing this
    // form reports place_id: null for a process that plainly has a place.
    const banner =
      "C:\\R\\RobloxStudioBeta.exe -task EditPlace -universeId 0 -placeId 95206881 " +
      "-userid 1183256136 -parentPid 9352 -parentSessionGuid 1B7B85EC-4838-4EE6-B1D5-FBA1496BC453";
    const got = parseIdentity(_log(banner));
    expect(got.place_id).toBe(95206881);
    expect(got.universe_id).toBe(0);
    expect(got.parent_pid).toBe(9352);
    expect(got.parent_session_guid).toBe("1B7B85EC-4838-4EE6-B1D5-FBA1496BC453");
  });
});

describe("the guids", () => {
  it("reads session and machine as distinct fields", () => {
    const got = parseIdentity(_log(GUID_LINES));
    expect(got.session_guid).toBe("45105BED-F1B1-4847-B213-FB0DD03F7E1B");
    expect(got.machine_guid).toBe("3FDE6FD7-3243-49EF-B961-0E81894BA3F0");
  });

  it("cannot confuse them", () => {
    // Both are 36 hex chars in the same channel, so a swapped field is invisible.
    // Machine GUID measured 2 distinct values across 45 logs, so treating it as
    // per-process would be wrong.
    const got = parseIdentity(_log(GUID_LINES + "\n" + PID_LINE));
    expect(got.session_guid).not.toBe(got.machine_guid);
    expect(got.pid).toBe(16240);
  });

  it("leaves session_guid in the log's own case", () => {
    // The asymmetry with parent_session_guid, which IS uppercased, is load-bearing:
    // every comparison between the two is case-insensitive, so a log that spelled
    // its own GUID in lower case must not read as a disagreement.
    const lower = "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] " +
      "Session GUID is 1b7b85ec-4838-4ee6-b1d5-fba1496bc453";
    expect(parseIdentity(_log(lower)).session_guid).toBe("1b7b85ec-4838-4ee6-b1d5-fba1496bc453");
  });

  it("uppercases parent_session_guid", () => {
    const banner =
      "C:\\R\\RobloxStudioBeta.exe -task EditPlace -parentSessionGuid 1b7b85ec-4838-4ee6-b1d5-fba1496bc453";
    expect(parseIdentity(_log(banner)).parent_session_guid).toBe(
      "1B7B85EC-4838-4EE6-B1D5-FBA1496BC453",
    );
  });
});

describe("the mesh-name join", () => {
  const identities = (): Map<number, LogIdentity> =>
    new Map<number, LogIdentity>([
      [
        16240,
        identity({
          place_path: "C:\\tmp\\robloxstudio-mcp-baseplates\\Baseplate-555883251.rbxl",
          place_id: null,
        }),
      ],
      [12324, identity({ place_path: null, place_id: 95206881 })],
      [19500, identity({ place_path: null, place_id: 95206881 })],
    ]);

  it("matches a file route on the basename", () => {
    expect(matchMeshName("Baseplate-555883251.rbxl", identities())).toEqual([16240]);
  });

  it("does not treat a full path as a mesh name", () => {
    // The mesh reports the basename, so a full path must match nothing rather than
    // falling through to some looser rule.
    expect(matchMeshName("C:\\tmp\\x\\Baseplate-555883251.rbxl", identities())).toEqual([]);
  });

  it("returns every URI candidate, not one", () => {
    // Two URI launches of one place are indistinguishable, and returning both is
    // what lets the caller say so instead of picking one.
    expect(
      [...matchMeshName("Template_95206881_AutoRecovery_3.rbxl", identities())].sort((a, b) => a - b),
    ).toEqual([12324, 19500]);
  });

  it("matches nothing for a null name", () => {
    // A server and its clients all report `name: null`, so this is a normal input,
    // not an error.
    expect(matchMeshName(null, identities())).toEqual([]);
  });

  it("lets a place basename win over a shared placeId", () => {
    // A file launch's basename is exact, so a shared placeId elsewhere must not
    // dilute it into a multi-candidate result.
    expect(matchMeshName("Baseplate-555883251.rbxl", identities())).toEqual([16240]);
  });
});

describe("ambiguity", () => {
  it("is silent on a single match", () => {
    expect(ambiguousReason("a.rbxl", [identity({ place_path: "a.rbxl" })])).toBeNull();
  });

  it("explains the null-name case", () => {
    expect(ambiguousReason(null, [])).toContain("no place name");
  });

  it("names the actual obstacle on the URI route", () => {
    // The reason has to point at the AutoRecovery counter, because that is what
    // actually blocks it and what a caller could otherwise work around.
    const reason = ambiguousReason("Template_1_AutoRecovery_2.rbxl", [
      identity({ place_id: 1 }),
      identity({ place_id: 1 }),
    ]);
    expect(reason).toContain("AutoRecovery");
  });

  it("counts several matches", () => {
    const reason = ambiguousReason("a.rbxl", [
      identity({ place_path: "a.rbxl" }),
      identity({ place_path: "a.rbxl" }),
      identity({ place_path: "a.rbxl" }),
    ]);
    expect(reason).toContain("3 logs");
  });

  it("says so when nothing matches", () => {
    expect(ambiguousReason("a.rbxl", [])).toContain("no Studio log mentions");
  });
});

describe("the launch direction", () => {
  it("matches a file launch exactly", () => {
    const id = identity({ place_path: "C:\\tmp\\Baseplate-9.rbxl", place_id: null });
    expect(nameMatchesIdentity("Baseplate-9.rbxl", id)).toBe(true);
    expect(nameMatchesIdentity("Baseplate-8.rbxl", id)).toBe(false);
  });

  it("matches a URI launch on the prefix only", () => {
    // The counter is unknowable from the log, so the prefix is the honest
    // assertion. A caller that accepts this must then check it is unique.
    const id = identity({ place_path: null, place_id: 95206881 });
    expect(nameMatchesIdentity("Template_95206881_AutoRecovery_7.rbxl", id)).toBe(true);
    expect(nameMatchesIdentity("Template_999_AutoRecovery_7.rbxl", id)).toBe(false);
  });

  it("never matches a null mesh name", () => {
    expect(
      nameMatchesIdentity(null, identity({ place_path: "C:\\tmp\\a.rbxl", place_id: 1 })),
    ).toBe(false);
  });

  it("agrees with the mesh-name direction", () => {
    // The two directions are separate code, so they can drift. Anything one accepts
    // the other must too, or a launch resolves one way and a stop the other.
    const cases: Array<[string | null, LogIdentity]> = [
      ["Baseplate-9.rbxl", identity({ place_path: "C:\\tmp\\Baseplate-9.rbxl", place_id: null })],
      ["Template_7_AutoRecovery_2.rbxl", identity({ place_path: null, place_id: 7 })],
      ["Baseplate-8.rbxl", identity({ place_path: "C:\\tmp\\Baseplate-9.rbxl", place_id: 7 })],
      [null, identity({ place_path: "C:\\tmp\\Baseplate-9.rbxl", place_id: 7 })],
    ];
    for (const [name, id] of cases) {
      const byName = matchMeshName(name, new Map([[1, id]])).length > 0;
      expect(byName, `disagreed on ${JSON.stringify(name)}`).toBe(nameMatchesIdentity(name, id));
    }
  });

  it("lists the names an identity could report", () => {
    // The mirror of matchMeshName, and the two must produce the same strings the
    // mesh would, or a launch cannot recognise its own process.
    expect(meshNamesForIdentity(identity({ place_path: "C:\\tmp\\a.rbxl", place_id: 7 }))).toEqual([
      "a.rbxl",
      "Template_7_AutoRecovery_",
    ]);
    expect(meshNamesForIdentity(identity())).toEqual([]);
  });
});

describe("process start times", () => {
  /**
   * `Get-CimInstance | ConvertTo-Json` hands back `/Date(ms)/`.
   *
   * An earlier version of the token join compared a log's start stamp to each
   * process's creation time, and could not parse this format. It failed silently,
   * skipping every process, which read as "no process matched" rather than as a
   * parser bug.
   */
  it("parses the CIM JSON form", () => {
    expect(parseProcessStarted("/Date(1790729920871)/")).toBeCloseTo(1790729920.871, 3);
  });

  it("parses an ISO string", () => {
    expect(parseProcessStarted("2026-09-30T09:18:20")).not.toBeNull();
  });

  it("passes a number through", () => {
    expect(parseProcessStarted(1790729920.871)).toBe(1790729920.871);
  });

  it("is null when unparseable, not zero", () => {
    // Zero is a real epoch, so a failure here would look like 1970 and put every
    // log outside the window.
    for (const value of ["", null, undefined, "not a date"]) {
      expect(parseProcessStarted(value), JSON.stringify(value)).toBeNull();
    }
  });

  it("parses the US-shaped forms", () => {
    expect(parseProcessStarted("09/30/2026 09:18:20")).not.toBeNull();
    expect(parseProcessStarted("09/30/2026 09:18:20 PM")).not.toBeNull();
  });

  it("honours an explicit offset", () => {
    // Python's `fromisoformat` does, so a caller whose only string is a UTC `Z` must
    // not be off by the host's offset. An *absent* zone is the local-time case above;
    // a present one is aware, so the same wall clock read as local would be off by the
    // offset on top of the offset.
    const zulu = parseProcessStarted("2026-09-30T09:18:20Z") as number;
    expect(new Date(zulu * 1000).toISOString()).toBe("2026-09-30T09:18:20.000Z");
    const plus3 = parseProcessStarted("2026-09-30T09:18:20+03:00") as number;
    expect(new Date(plus3 * 1000).toISOString()).toBe("2026-09-30T06:18:20.000Z");
  });

  it("reads an unzoned time as local, like a naive Python datetime", () => {
    // The filter compares a UTC filename stamp against this, so reading the wall
    // clock as UTC here would shift the window by the host's offset - three hours on
    // the measuring machine - and every file would fall outside it.
    const naive = parseProcessStarted("2026-09-30T09:18:20") as number;
    expect(new Date(naive * 1000).getFullYear()).toBe(2026);
    expect(new Date(naive * 1000).getHours()).toBe(9);
    expect(naive).toBeLessThan((parseProcessStarted("2026-09-30T09:18:20Z") as number) + 86_400);
  });

  it("rejects a day that does not exist rather than rolling it over", () => {
    // Python's `_strptime` ends by constructing a `date`, so 31 February is a
    // ValueError. `Date.UTC` would silently become 3 March, which is the same class
    // of silent-days-wrong error the UTC stamp exists to prevent.
    expect(parseProcessStarted("2026-02-31T09:18:20")).toBeNull();
  });
});

describe("the filename stamp", () => {
  it("parses it, and as UTC", () => {
    const seconds = stampSeconds("0.741.19.7411056_20260930T091820Z_Studio_D4ED5_last.log");
    expect(seconds).not.toBeNull();
    // The `Z` is load-bearing: a naive parse is local, which on the measuring
    // machine (UTC+3) was three hours early - and a process start time is exactly
    // where a three-hour error hides rather than shows.
    expect(new Date((seconds as number) * 1000).toISOString()).toBe("2026-09-30T09:18:20.000Z");
  });

  it("is null when missing", () => {
    expect(stampSeconds("RobloxStudioInstaller_0EA05.log")).toBeNull();
  });

  it("is null for a stamp that is not a real date", () => {
    expect(stampSeconds("x_20260231T091820Z_x.log")).toBeNull();
    expect(stampSeconds("x_20261330T091820Z_x.log")).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// test_logid_scaling.py - the prefix read
// --------------------------------------------------------------------------- //

const SCALE_BANNER =
  "C:\\Roblox\\Versions\\version-x\\RobloxStudioBeta.exe --task EditFile " +
  "--localPlaceFile C:\\tmp\\Baseplate-1.rbxl";
const SCALE_PID =
  "2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] " +
  "Constructing UIThreadNotifier for process '4242' with id " +
  "'https://www.roblox.com-Studio'";
const FILLER = "2026-09-30T09:16:55.000Z,2.0,1a78,6,Info [FLog::Somewhere] padding line\n";

describe("the prefix read", () => {
  let dir = "";

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "logid-prefix-"));
  });

  afterEach(() => {
    setPrefixBytes(null);
    setLogDirectory(null);
    rmSync(dir, { recursive: true, force: true });
  });

  const write = (name: string, text: string) => {
    const path = join(dir, name);
    writeFileSync(path, text, "utf8");
    return path;
  };

  it("reads an identity inside the prefix", () => {
    setPrefixBytes(8192);
    const got = readIdentity(write("near.log", SCALE_BANNER + "\n" + SCALE_PID))!;
    expect(got.pid).toBe(4242);
    expect(got.place_path).toMatch(/Baseplate-1\.rbxl$/);
    expect(got.task).toBe("EditFile");
  });

  it("falls back when the pid is past the prefix", () => {
    // The prefix holds the banner but not the PID. A resolver that trusted the
    // prefix would report this live process as having no identity at all, and it
    // would silently drop out of the join.
    setPrefixBytes(256);
    const got = readIdentity(write("far.log", SCALE_BANNER + "\n" + FILLER.repeat(40) + SCALE_PID))!;
    expect(got.pid).toBe(4242);
  });

  it("reports no pid rather than guessing when absent everywhere", () => {
    setPrefixBytes(256);
    expect(readIdentity(write("none.log", SCALE_BANNER + "\n" + FILLER.repeat(40)))!.pid).toBeNull();
  });

  it("is null for a missing file, not an exception", () => {
    // A log being rotated or locked is normal, and one unreadable file must not fail
    // the whole sweep.
    expect(readIdentity(join(dir, "absent.log"))).toBeNull();
  });

  it("keeps the measured default rather than a guessed one", () => {
    // All four identity lines fit in 3,809 bytes across 43 measured logs, so the
    // 64 KB default is a ~17x margin. If someone lowers it to near 4 KB the margin
    // disappears silently, so pin it.
    expect(PREFIX_BYTES).toBeGreaterThanOrEqual(8192);
  });

  it("keeps a prefix default that reaches the PlaceSessionId lines", () => {
    // Measured: numeric-form PlaceSessionId lines sit at 64,520 to 74,960 bytes. A
    // 64 KB prefix missed every one, which is how two logs of this project came to
    // say the counter was not recorded at all.
    expect(PREFIX_BYTES).toBeGreaterThanOrEqual(81920);
  });

  it("keeps a prefix small enough to stay cheap", () => {
    // Per-file cost is 0.100 ms at 64 KB, 0.130 ms at 256 KB, 0.626 ms at 1 MB. 256 KB
    // is the knee; past it the sweep cost multiplies.
    expect(PREFIX_BYTES).toBeLessThanOrEqual(512 * 1024);
  });

  it("does not split a multi-byte character at the prefix boundary", () => {
    // Python reads in *text* mode, so a 262144-character read can never split a
    // multi-byte sequence. A byte-sized Node read can, and the U+FFFD it would leave
    // behind is deleted by the ignore-on-decode rule - taking the log's last
    // character with it.
    setPrefixBytes(8);
    const text = "1234567é\n" + SCALE_PID;
    expect(readIdentity(write("utf8.log", text))!.pid).toBe(4242);
  });
});

// --------------------------------------------------------------------------- //
// test_logid.py - the start-time window
// --------------------------------------------------------------------------- //

describe("the start-time window", () => {
  let dir = "";

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "logid-window-"));
    setLogDirectory(() => dir);
  });

  afterEach(() => {
    setLogDirectory(null);
    rmSync(dir, { recursive: true, force: true });
  });

  const write = (pid: number, stampText = "20260930T120000Z") => {
    const path = join(dir, `0.741.19.7411056_${stampText}_Studio_0000A_last.log`);
    writeFileSync(
      path,
      FILE_BANNER +
        "\n" +
        `2026-09-30T12:00:00.100Z,0.1,1a78,6,Info [FLog::UIThreadNotifier] ` +
        `Constructing UIThreadNotifier for process '${pid}' with id 'x'`,
      "utf8",
    );
    return 1790769600; // 2026-09-30T12:00:00Z
  };

  it("falls back when the window excludes everything", () => {
    // A too-narrow window must cost time, not an identity. If this regressed, a live
    // Studio would silently drop out of the join.
    const stamp = write(4242);
    const absurd = new Map([[4242, stamp - 86400 * 365]]);
    expect(liveIdentities([4242], absurd).get(4242)?.pid).toBe(4242);
  });

  it("finds the log when the window includes it", () => {
    const stamp = write(4343);
    expect(liveIdentities([4343], new Map([[4343, stamp]])).get(4343)?.pid).toBe(4343);
  });

  it("is a full sweep when no window is given", () => {
    write(4444);
    expect(liveIdentities([4444]).has(4444)).toBe(true);
  });

  it("is empty when no pids are asked for", () => {
    write(4545);
    expect(liveIdentities([]).size).toBe(0);
  });

  it("stamps the identity it found", () => {
    const stamp = write(4646);
    const got = liveIdentities([4646], new Map([[4646, stamp]])).get(4646)!;
    expect(got.started).toBe(stamp);
    expect(got.log).toBe("0.741.19.7411056_20260930T120000Z_Studio_0000A_last.log");
  });
});

// --------------------------------------------------------------------------- //
// test_session_guid.py - the AutoRecovery counter
// --------------------------------------------------------------------------- //

/** Verbatim shapes from pid 12324's log on the measuring machine. */
const NUMERIC =
  "a,b,c,6 [telemetryLog] PlaceSessionId: " +
  "A3A73B66-0B94-4C5E-AA26-5F44551274BC-95206881";
const PATH_FORM =
  "a,b,c,6 [telemetryLog] PlaceSessionId: " +
  "77C06B69-0B74-4C0B-B082-39E5D0EC16BB-" +
  "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves" +
  "\\Template_95206881_AutoRecovery_3.rbxl";
const DM_ID =
  "a,b,c,6 [telemetryLog] DmId: 77C06B69-0B74-4C0B-B082-39E5D0EC16BB-" +
  "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves" +
  "\\Template_95206881_AutoRecovery_3.rbxl-StudioGameStateType_Edit";

describe("session GUIDs", () => {
  it("yields the guid from both suffix forms", () => {
    const guids = sessionGuids(NUMERIC + "\n" + PATH_FORM);
    expect(guids).toContain("A3A73B66-0B94-4C5E-AA26-5F44551274BC");
    expect(guids).toContain("77C06B69-0B74-4C0B-B082-39E5D0EC16BB");
  });

  it("takes a DmId's guid too", () => {
    expect(sessionGuids(DM_ID)).toEqual(["77C06B69-0B74-4C0B-B082-39E5D0EC16BB"]);
  });

  it("collapses repeats but keeps order", () => {
    // One measured log had 29 PlaceSessionId lines carrying 2 distinct GUIDs.
    // Returning 29 entries would be useless as an identity.
    expect(sessionGuids((NUMERIC + "\n").repeat(13))).toEqual([
      "A3A73B66-0B94-4C5E-AA26-5F44551274BC",
    ]);
  });

  it("is empty, not null, when there are none", () => {
    expect(sessionGuids("nothing here")).toEqual([]);
  });

  it("de-duplicates across the two forms without reordering", () => {
    expect(sessionGuids(PATH_FORM + "\n" + DM_ID)).toEqual([
      "77C06B69-0B74-4C0B-B082-39E5D0EC16BB",
    ]);
  });
});

describe("the place-session path", () => {
  it("yields the path form", () => {
    expect(placeSessionPath(PATH_FORM)).toBe(
      "C:/Users/User/AppData/Local/Roblox/RobloxStudio/AutoSaves" +
        "\\Template_95206881_AutoRecovery_3.rbxl",
    );
  });

  it("yields nothing for the numeric form", () => {
    // It names a published place, not a file, so there is no counter in it.
    // Returning something here would invent a path that does not exist.
    expect(placeSessionPath(NUMERIC)).toBeNull();
  });

  it("recovers the counter from the path", () => {
    const path = placeSessionPath(PATH_FORM)!;
    expect(path).toContain("_AutoRecovery_3.rbxl");
  });

  it("is null for an empty log", () => {
    expect(placeSessionPath("")).toBeNull();
  });

  it("is null for an unreadable file", () => {
    expect(readPlaceSessionPath(join(tmpdir(), "no-such-dir", "absent.log"))).toBeNull();
  });
});

describe("narrowing URI candidates by the counter", () => {
  let dir = "";

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "logid-refine-"));
    setLogDirectory(() => dir);
    writeFileSync(join(dir, "a.log"), PATH_FORM, "utf8");
    writeFileSync(join(dir, "b.log"), NUMERIC, "utf8");
  });

  afterEach(() => {
    setLogDirectory(null);
    rmSync(dir, { recursive: true, force: true });
  });

  const twoCandidates = (): Map<number, LogIdentity> =>
    new Map<number, LogIdentity>([
      [111, identity({ place_id: 95206881, log: "a.log" })],
      [222, identity({ place_id: 95206881, log: "b.log" })],
    ]);

  it("separates two URI launches by the counter", () => {
    // The case this project called unresolvable. Both candidates share a placeId;
    // only one log records the autorecovery path, and that is the one the mesh name
    // refers to.
    expect(matchMeshName("Template_95206881_AutoRecovery_3.rbxl", twoCandidates())).toEqual([111]);
  });

  it("keeps the candidates when no log records that counter", () => {
    // A mesh name whose counter matches nothing must not resolve to nothing.
    // Returning `[]` would claim no such Studio exists, which is a worse wrong
    // answer than "I cannot tell which of these two it is".
    const got = matchMeshName("Template_95206881_AutoRecovery_9.rbxl", twoCandidates());
    expect([...got].sort((a, b) => a - b)).toEqual([111, 222]);
  });

  it("keeps the candidates when no log records a path at all", () => {
    // Both opened a plain unsaved document, so neither has a counter. Narrowing to
    // one here would be a coin flip presented as a match.
    writeFileSync(join(dir, "a.log"), NUMERIC, "utf8");
    const got = matchMeshName("Template_95206881_AutoRecovery_3.rbxl", twoCandidates());
    expect([...got].sort((a, b) => a - b)).toEqual([111, 222]);
  });

  it("does not refine a single candidate", () => {
    // No reason to do a whole-file read when the answer is already unique.
    const identities = new Map<number, LogIdentity>([
      [111, identity({ place_id: 95206881, log: "a.log" })],
    ]);
    expect(matchMeshName("Template_95206881_AutoRecovery_3.rbxl", identities)).toEqual([111]);
  });
});

// --------------------------------------------------------------------------- //
// test_logid_formats.py - line-format recognition
// --------------------------------------------------------------------------- //

describe("type four is unchanged", () => {
  it("recognises the real pid line", () => {
    expect(classifyLine(PID_LINE_T4)).toBe(DATA_FORMAT_TYPE4);
  });

  it("recognises the no-severity variant", () => {
    // 29.6% of measured lines. If the optional severity field is dropped, this is
    // what breaks - and it would break *loudly*, on a third of every log, which is
    // worse than breaking on none.
    expect(classifyLine(PID_LINE_T4_NO_SEVERITY)).toBe(DATA_FORMAT_TYPE4);
  });

  it("accepts every measured severity word", () => {
    // Six words measured, not the five the spec lists. A closed set rejects the
    // sixth the day it ships, so this pins the open behaviour.
    for (const word of ["Info", "Warning", "Error", "Debug", "Verbose", "Critical"]) {
      const line = `2026-09-29T21:59:14.167Z,0.167001,55e8,6,${word} [FLog::Output] x`;
      expect(classifyLine(line), word).toBe(DATA_FORMAT_TYPE4);
    }
  });

  it("accepts a truncated record", () => {
    // 74 measured lines lost their channel to a mid-write truncation. These are real
    // records with a valid prefix, not a format we failed to read.
    const line = "2026-09-29T22:11:31.862Z,21.862654,4600,6,Critical <log line elided>";
    expect(classifyLine(line)).toBe(DATA_FORMAT_TYPE4);
  });

  it("accepts a channel containing a space and a plus", () => {
    // `[LOGCHANNELS + 1]`, measured on 2 lines. A `Name::SubName` channel pattern
    // would reject it.
    const line =
      "2026-09-29T22:11:34.700Z,19.700460,33cc,6 [LOGCHANNELS + 1] RBXCRASH: OutOfMemory";
    expect(classifyLine(line)).toBe(DATA_FORMAT_TYPE4);
  });
});

describe("the other formats are recognised", () => {
  it("type one", () => {
    expect(classifyLine(PID_LINE_T1)).toBe(DATA_FORMAT_TYPE1);
  });

  it("type one with a severity word instead of the numeric field", () => {
    expect(classifyLine(PID_LINE_T1_SEVERITY)).toBe(DATA_FORMAT_TYPE1);
  });

  it("type two", () => {
    expect(classifyLine(PID_LINE_T2)).toBe(DATA_FORMAT_TYPE2);
  });

  it("type three", () => {
    expect(classifyLine(PID_LINE_T3)).toBe(DATA_FORMAT_TYPE3);
  });

  it("separates type one from type two on the channel marker alone", () => {
    // The same fields, differing only by `[FLog::...]`. If the recogniser ordered on
    // timestamp shape alone it could not tell them apart, because there is nothing
    // else to tell them apart by.
    expect(classifyLine(PID_LINE_T1)).not.toBe(classifyLine(PID_LINE_T2));
  });

  it("finds the pid in every format", () => {
    // The whole point. A type-1 log that states its PID must report it.
    for (const line of [
      PID_LINE_T4,
      PID_LINE_T4_NO_SEVERITY,
      PID_LINE_T1,
      PID_LINE_T1_SEVERITY,
      PID_LINE_T2,
      PID_LINE_T3,
    ]) {
      expect(parseIdentity(lines(FORMATS_BANNER, line)).pid, line.slice(0, 40)).toBe(16240);
    }
  });

  it("reads the banner identically in every format", () => {
    // The command line is untimestamped in all four formats, so the place and task
    // must parse identically whichever format the records use.
    for (const line of [PID_LINE_T4, PID_LINE_T1, PID_LINE_T2, PID_LINE_T3]) {
      const got = parseIdentity(lines(FORMATS_BANNER, line));
      expect(got.place_path).toMatch(/Baseplate-555883251\.rbxl$/);
      expect(got.task).toBe("EditFile");
    }
  });
});

describe("destructing is not a pid", () => {
  const DESTRUCTING =
    "2026-09-29T21:58:20.118Z,8.118437,58d4,6,Info [FLog::UIThreadNotifier] " +
    "Destructing UIThreadNotifier for process '3000' with id 'x'";

  it("does not yield a pid", () => {
    // The teardown half of the pair. If it ever matched, a Studio that shut down
    // would re-report a PID for a process that is no longer there.
    expect(parseIdentity(lines(FORMATS_BANNER, DESTRUCTING)).pid).toBeNull();
  });

  it("lets the constructing line win over a later destructing one", () => {
    const teardown = PID_LINE_T4.replaceAll("16240", "3000").replaceAll(
      "Constructing",
      "Destructing",
    );
    expect(parseIdentity(lines(FORMATS_BANNER, PID_LINE_T4, teardown)).pid).toBe(16240);
  });
});

describe("unrecognised is loud", () => {
  it("flags a garbage line rather than ignoring it", () => {
    expect(classifyLine(GARBAGE)).toBe(DATA_UNRECOGNISED);
  });

  it("reports a garbage log as a format problem rather than as no pid", () => {
    // The distinction this module exists for. Both cases give `pid === null`; only
    // one of them is a parser gap, and the caller needs to tell them apart to act on
    // the right one.
    const got = parseIdentity(lines(FORMATS_BANNER, GARBAGE, GARBAGE));
    expect(got.pid).toBeNull();
    expect(got.unrecognised_lines).toBe(2);
    expect(noPidReason(got)).not.toBeNull();
  });

  it("does not call a genuinely pidless log a format problem", () => {
    // A Studio that died before writing the notifier line. This must report zero
    // unrecognised lines, or the loud signal becomes noise and a caller learns to
    // ignore it - which is the same as having no signal at all.
    const got = parseIdentity(
      lines(FORMATS_BANNER, "2026-09-30T09:12:05.345Z,0.345598,0edc,6,Info [FLog::Output] hi"),
    );
    expect(got.pid).toBeNull();
    expect(got.unrecognised_lines).toBe(0);
    expect(got.log_format).toBe(DATA_FORMAT_TYPE4);
    expect(noPidReason(got)).toBeNull();
  });

  it("says in words when a log is pure garbage", () => {
    // Not "predominant format is null", which is precise and tells a reader nothing.
    // Nothing was readable at all, so that is what it should say.
    const got = parseIdentity(lines(FORMATS_BANNER, GARBAGE));
    expect(got.log_format).toBeNull();
    expect(noPidReason(got)).toContain("No line in the log matched");
  });

  it("censors on demand rather than on every read", () => {
    // A log with a readable PID line *and* some unreadable ones. The identity census
    // is deliberately skipped whenever a PID is found, and this is the test that
    // says so rather than leaving it to be discovered. The cost is not hypothetical:
    // at 256 KB, `formatReport` measures **2.210 ms** against **0.004 ms** for the PID
    // search it would accompany - 622x, and 3504x at 1 MB. Paying that on every file
    // in a multi-thousand-log sweep to re-derive a verdict a found PID has already
    // answered is a bad trade, so the full census is the caller's, and it is exact
    // when they make it.
    const text = lines(FORMATS_BANNER, PID_LINE_T4, PID_LINE_T4, PID_LINE_T4, GARBAGE);
    const got = parseIdentity(text);
    expect(got.pid).toBe(16240);
    expect(got.unrecognised_lines).toBeNull();
    expect(noPidReason(got)).toBeNull();

    // And when the caller does ask, the answer is the accurate one.
    const report = formatReport(text);
    expect(report.unrecognised).toBe(1);
    expect(report.dominant).toBe(DATA_FORMAT_TYPE4);
  });

  it("names the known format alongside the garbage", () => {
    // The case that motivates the reason string: mostly readable, one line in no
    // known format, and no PID. The readable lines are ordinary type-4 records that
    // simply do not carry a PID, standing in for "the readable part is type 4"
    // without handing the test a PID, which would short-circuit the census.
    const ordinary =
      "2026-09-30T09:12:05.345Z,0.345598,0edc,6,Info [FLog::StudioMain] starting up";
    const got = parseIdentity(lines(FORMATS_BANNER, ordinary, ordinary, GARBAGE));
    expect(got.pid).toBeNull();
    expect(got.log_format).toBe(DATA_FORMAT_TYPE4);
    const reason = noPidReason(got)!;
    expect(reason).toContain("parser gap");
    expect(reason).toContain("type4");
  });

  it("owes no reason when the pid was found", () => {
    expect(noPidReason(parseIdentity(lines(PID_LINE_T4)))).toBeNull();
  });

  it("returns the sample lines so a caller can see them", () => {
    const report = formatReport(lines(FORMATS_BANNER, GARBAGE, GARBAGE));
    expect(report.unrecognised_lines).toHaveLength(2);
    expect(report.unrecognised_lines[0]).toContain("do not read");
  });

  it("treats a torn timestamp as unrecognised rather than a continuation", () => {
    // Measured once, in the installer log. Half a timestamp is a truncated write,
    // and it is the one line in the corpus with record intent and no parseable
    // prefix.
    expect(classifyLine("2026-09-29T21:56:01.194Z")).toBe(DATA_UNRECOGNISED);
  });
});

describe("known non-records are not failures", () => {
  it("reads the terms-of-use preamble as preamble", () => {
    const line =
      "[FLog::Output] All use of Roblox services must comply with Roblox's Terms of Use";
    expect(classifyLine(line)).toBe(DATA_PREAMBLE);
  });

  it("reads the command-line block as preamble", () => {
    expect(classifyLine("Command line:")).toBe(DATA_PREAMBLE);
    expect(classifyLine("*******")).toBe(DATA_PREAMBLE);
  });

  it("does not let the preamble make a log look unrecognised", () => {
    // A real log opens with the preamble on 67 of 67 files measured. If that counted
    // as unrecognised, every log would report a problem. Asserted on `formatReport`
    // rather than on `parseIdentity`, because the identity census only runs when
    // there is no PID to explain - and this log has one.
    const report = formatReport(
      lines(
        "[FLog::Output] All use of Roblox services must comply",
        "*******",
        "Command line:",
        FORMATS_BANNER,
        PID_LINE_T4,
      ),
    );
    expect(report.unrecognised).toBe(0);
    expect(report.counts[DATA_PREAMBLE]).toBe(3);
  });

  it("reads a stack trace as a continuation", () => {
    expect(classifyLine("Script 'MaterialManager.Packages._Index', Line 65 - function profileend")).toBe(
      DATA_CONTINUATION,
    );
  });

  it("reads a json fragment as a continuation", () => {
    expect(classifyLine('\t"code": 100,')).toBe(DATA_CONTINUATION);
  });

  it("does not treat a blank line as an error", () => {
    expect(classifyLine("")).toBe(DATA_CONTINUATION);
    expect(classifyLine("   ")).toBe(DATA_CONTINUATION);
  });

  it("reads the log's own banner as a continuation", () => {
    // It is untimestamped and carries no channel marker, so it is the *absence* of
    // a record rather than a format nobody read.
    expect(classifyLine(FILE_BANNER)).toBe(DATA_CONTINUATION);
  });
});

describe("the report shape", () => {
  it("names the format that appears most", () => {
    const report = formatReport(lines(PID_LINE_T4, PID_LINE_T4_NO_SEVERITY, PID_LINE_T1));
    expect(report.dominant).toBe(DATA_FORMAT_TYPE4);
    expect(report.counts[DATA_FORMAT_TYPE1]).toBe(1);
  });

  it("reports a log of only older formats as that format", () => {
    // The version of the signal that matters: a type-1 log must not claim to be type
    // 4, or a caller cannot tell which parser rules were applied.
    expect(formatReport(lines(PID_LINE_T1, PID_LINE_T1, PID_LINE_T1)).dominant).toBe(
      DATA_FORMAT_TYPE1,
    );
  });

  it("gives every format a key even at zero", () => {
    // A missing key and a zero are different, and only one of them means "we looked
    // and found none".
    const counts = formatReport(lines(PID_LINE_T4)).counts;
    expect(Object.keys(counts).sort()).toEqual([...DATA_FORMATS].sort());
  });

  it("has no dominant format when a log holds no records", () => {
    expect(formatReport("nothing here\n").dominant).toBeNull();
  });

  it("is all zeroes on an empty log, not an error", () => {
    const report = formatReport("");
    expect(report.unrecognised).toBe(0);
    expect(report.dominant).toBeNull();
  });

  it("counts every line exactly once", () => {
    const text = lines(PID_LINE_T4, GARBAGE, "Command line:", "*******", "");
    const total = Object.values(formatReport(text).counts).reduce((a, b) => a + b, 0);
    expect(total).toBe(5);
  });

  it("counts a CRLF log the same way as an LF one", () => {
    // Python read these files in text mode, which normalised CRLF, so a byte-for-byte
    // Node read has to reach the same count or the census means something different
    // on the two servers. `text.split("\n")` would also add a trailing empty line
    // per file, which Python's `str.splitlines()` does not.
    const lf = lines(PID_LINE_T4, GARBAGE, "Command line:", "*******", "");
    const crlf = lf.replaceAll("\n", "\r\n");
    const lfTotal = Object.values(formatReport(lf).counts).reduce((a, b) => a + b, 0);
    const crlfTotal = Object.values(formatReport(crlf).counts).reduce((a, b) => a + b, 0);
    expect(crlfTotal).toBe(lfTotal);
    expect(crlfTotal).toBe(5);
  });
});

describe("backwards compatibility", () => {
  it("keeps every pre-existing key present", () => {
    // The Python dict was read by key from other modules and older tests, so the key
    // names are fixed even where the *value* types are now checked.
    const got = parseIdentity(lines(FORMATS_BANNER, PID_LINE_T4));
    const keys: Array<keyof LogIdentity> = [
      "pid",
      "place_path",
      "place_id",
      "universe_id",
      "task",
      "parent_pid",
      "parent_session_guid",
      "transport_token",
      "session_guid",
      "machine_guid",
    ];
    for (const key of keys) expect(got[key], key).toBeDefined();
  });

  it("leaves the new keys null when the pid parsed", () => {
    // `null` here means "not checked, and not needed" - a log whose PID line parsed
    // is not evidence of a format problem, and running a full census on every read
    // would cost the sweep more than it is worth.
    const got = parseIdentity(lines(FORMATS_BANNER, PID_LINE_T4));
    expect(got.log_format).toBeNull();
    expect(got.unrecognised_lines).toBeNull();
  });

  it("omits `log` from a bare parse and adds it on a read", () => {
    // `log` and `started` are optional rather than null-valued precisely because a
    // bare parse has no file to name, and saying `log: null` would be a claim that
    // the log was absent rather than that it was never consulted.
    expect(parseIdentity(lines(PID_LINE_T4)).log).toBeUndefined();
    const got = readIdentity(
      writeTemp(lines(FORMATS_BANNER, PID_LINE_T4), "0.741_20260930T091205Z_Studio_75655_last.log"),
    )!;
    expect(got.log).toBe("0.741_20260930T091205Z_Studio_75655_last.log");
    expect(got.pid).toBe(16240);
  });

  it("reports an unreadable file as null", () => {
    expect(readIdentity(join(tmpdir(), "logid-absent-dir", "absent.log"))).toBeNull();
  });

  it("keeps the pid a number and never zero", () => {
    const got = parseIdentity(lines(FORMATS_BANNER, PID_LINE_T4));
    expect(typeof got.pid).toBe("number");
    expect(got.pid).not.toBe(0);
  });
});

/** Write a one-shot temp file and return its path. */
function writeTemp(text: string, name: string): string {
  const dir = mkdtempSync(join(tmpdir(), "logid-one-"));
  const path = join(dir, name);
  writeFileSync(path, text, "utf8");
  return path;
}

// --------------------------------------------------------------------------- //
// test_logid_outcome.py - why a place did not open
// --------------------------------------------------------------------------- //

const FAIL_FETCH =
  "a,b,c,6 [telemetryLog] State: OpenPlaceInitialization\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceCreateDataModel\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceLoadDataModel\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceFailure\n" +
  "a,b,c,6 [telemetryLog] ErrorType: DataModelLoadingFailure\n" +
  "a,b,c,6 [telemetryLog] ErrorMessage: Error fetching latest place version\n" +
  "a,b,c,6 [telemetryLog] TaskNames: OpenPlaceFailure;\n";

const FAIL_CONN =
  "a,b,c,6 [telemetryLog] State: OpenPlaceFailure\n" +
  "a,b,c,6 [telemetryLog] ErrorType: DataModelLoadingFailure\n" +
  "a,b,c,6 [telemetryLog] ErrorMessage: Connection error 279\n";

const SUCCESS = "a,b,c,6 [telemetryLog] State: OpenPlaceSuccess\n";

/**
 * The real tail of a successful Edit launch. `OpenPlacePreSuccess` is a progress state
 * whose name ends in "Success", and the sequence continues past the terminal state to
 * `PlaceIdle` - both of which break a naive parse.
 */
const PROGRESS_THEN_SUCCESS =
  "a,b,c,6 [telemetryLog] State: OpenPlaceInitialization\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceCreateDataModel\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceLoadDataModel\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceWaitForStreaming\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlacePostLoadDataModel\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceEnterDataModelScope\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlacePreSuccess\n" +
  "a,b,c,6 [telemetryLog] State: OpenPlaceSuccess\n" +
  "a,b,c,6 [telemetryLog] Workflow: OpenPlace\n" +
  "a,b,c,6 [telemetryLog] State: PlaceIdle\n";

const HANG_IN_PROGRESS =
  "x,Warning [FLog::StudioHangMonitor] Hang In Progress. HangId: 1, " +
  "ServiceId: MainThreadHangs\n";
const SIGNED_IN =
  "x [FLog::LoginController] Login got Standalone DM ready to enter User scope\n";
const INSTANCES = "x [FLog::SystemCheck] Running instance count at launch 5\n";

describe("the open outcome", () => {
  it("reads the failure reason", () => {
    const got = openOutcome(FAIL_FETCH);
    expect(got.state).toBe("OpenPlaceFailure");
    expect(got.error_type).toBe("DataModelLoadingFailure");
    expect(got.error_message).toBe("Error fetching latest place version");
    expect(got.opened).toBe(false);
  });

  it("distinguishes the two failure kinds", () => {
    // Measured 3:1 across four real failures. Conflating them would send the reader
    // after the wrong problem - a transport retry against a place fetch.
    expect(openOutcome(FAIL_FETCH).error_message).toBe("Error fetching latest place version");
    expect(openOutcome(FAIL_CONN).error_message).toBe("Connection error 279");
  });

  it("reads a real success sequence as success", () => {
    // Reporting `PlaceIdle` as the state made 21 successful launches look like zero
    // opened, because the sequence does not stop at the terminal state.
    const got = openOutcome(PROGRESS_THEN_SUCCESS);
    expect(got.opened).toBe(true);
    expect(got.state).toBe("OpenPlaceSuccess");
  });

  it("does not read PreSuccess as a success", () => {
    // `OpenPlacePreSuccess` ends with "Success" and is a progress state. A suffix
    // test would call a half-finished load a success, which is the worst possible
    // direction to be wrong in.
    const got = openOutcome(
      "a,b,c,6 [telemetryLog] State: OpenPlacePreSuccess\n" +
        "a,b,c,6 [telemetryLog] State: PlaceIdle\n",
    );
    expect(got.opened).toBe(false);
    expect(got.state).toBeNull();
  });

  it("does not let PlaceIdle clear a failure", () => {
    // Idle is a progress state, so it must not overwrite a recorded failure.
    const got = openOutcome(FAIL_FETCH + "a,b,c,6 [telemetryLog] State: PlaceIdle\n");
    expect(got.opened).toBe(false);
    expect(got.error_message).toBe("Error fetching latest place version");
  });

  it("lets a later success clear an earlier failure", () => {
    // Studio retries. First-failure-wins would report a healthy process as broken,
    // which is worse than reporting nothing.
    const got = openOutcome(FAIL_FETCH + FAIL_FETCH + SUCCESS);
    expect(got.opened).toBe(true);
    expect(got.state).toBe("OpenPlaceSuccess");
    expect(got.error_message).toBeNull();
    expect(got.error_type).toBeNull();
  });

  it("takes the last failure when it never recovers", () => {
    expect(openOutcome(FAIL_FETCH + FAIL_CONN).error_message).toBe("Connection error 279");
  });

  it("does not treat Hang In Progress as a hang", () => {
    // This appears in logs that opened their place fine, including one with 5
    // instances already running. Treating it as a hang would invent a fault on a
    // healthy Studio.
    const got = openOutcome(HANG_IN_PROGRESS + SUCCESS);
    expect(got.opened).toBe(true);
    expect(got.state).toBe("OpenPlaceSuccess");
    expect(got.error_message).toBeNull();
  });

  it("does not let Hang In Progress clear a failure", () => {
    expect(openOutcome(FAIL_FETCH + HANG_IN_PROGRESS).error_message).toBe(
      "Error fetching latest place version",
    );
  });

  it("reads sign-in and the instance count", () => {
    const got = openOutcome(SIGNED_IN + INSTANCES + SUCCESS);
    expect(got.signed_in).toBe(true);
    expect(got.instances_at_launch).toBe(5);
  });

  it("makes never-signed-in distinguishable", () => {
    // Two real logs never reached sign-in and spun on 403s. That is a different
    // failure from a place that would not fetch, and it is visible.
    const got = openOutcome("x [DFLog::HttpTraceError] status:403 Forbidden\n");
    expect(got.signed_in).toBe(false);
    expect(got.state).toBeNull();
    expect(got.instances_at_launch).toBeNull();
  });

  it("is all-null on an empty log, not an error", () => {
    const got = openOutcome("");
    expect(got.state).toBeNull();
    expect(got.error_message).toBeNull();
    expect(got.opened).toBe(false);
  });

  it("does not match an error message outside a failure", () => {
    // Without the failure gate, any stray ErrorMessage line would attach itself to
    // whatever the last state happened to be.
    const got = openOutcome(
      SUCCESS + "a,b,c,6 [telemetryLog] ErrorMessage: unrelated later noise\n",
    );
    expect(got.error_message).toBeNull();
  });
});

describe("reading the outcome from a file", () => {
  it("reads past the identity prefix", () => {
    // The outcome lines sit wherever the load finished, not in the first 4 KB. A
    // prefix read would report "no outcome" and that is the whole question this
    // function answers.
    const path = writeTemp("padding line\n".repeat(200_000) + FAIL_FETCH, "deep.log");
    expect(readOpenOutcome(path)?.error_message).toBe("Error fetching latest place version");
  });

  it("adds the log name", () => {
    const path = writeTemp(FAIL_FETCH, "outcome.log");
    expect(readOpenOutcome(path)?.log).toBe("outcome.log");
  });

  it("is null for a missing file", () => {
    expect(readOpenOutcome(join(tmpdir(), "logid-absent-dir", "absent.log"))).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// test_parent_edge.py - the -parentPid edge
// --------------------------------------------------------------------------- //

const EXE = "C:\\Users\\User\\AppData\\Local\\Roblox\\Versions\\version-76e1a02649ad4f35\\RobloxStudioBeta.exe";
const PROJECT =
  "C:/Users/User/AppData/Local/Temp/robloxstudio-mcp-baseplates/Baseplate-1340472086.rbxl";

/** The Edit Studio that pressed Play. Named, so reachable by the name join. */
const EDIT_PID = 22656;
/**
 * The server, and the client it spawned. TODO records this shape: one server, several
 * clients, all parented to the server, the server parented to the Edit Studio.
 */
const SERVER_PID = 19028;
const CLIENT_PIDS = [19029, 19030, 19031] as const;

const EDIT_SESSION = "1B7B85EC-4838-4EE6-B1D5-FBA1496BC453";
const SERVER_SESSION = "4C2A10D7-9E33-4A18-8B0C-77A1E5C9D210";
const CLIENT_SESSION = "9D31AA02-6F17-4B44-91C5-3E0B62D48A77";

const editBanner = (): string => `${EXE} --task EditFile --localPlaceFile ${PROJECT}`;
const serverBanner = (parent: number = EDIT_PID): string =>
  `${EXE} -placeVersion 0 -creatorId 0 -task StartServer -localProjectFile ${PROJECT} -parentPid ${parent}`;
const clientBanner = (parent: number = SERVER_PID): string =>
  `${EXE} -task StartClient -rbxTransportToken bG9jYWxfdGVzdA== -localProjectFile ${PROJECT} -parentPid ${parent}`;
/** The fourth launch route: File > New. A child, and a *named* one. */
const fileNewBanner = (parent: number = EDIT_PID): string =>
  `${EXE} -task EditPlace -universeId 0 -placeId 95206881 -userid 1183256136 ` +
  `-parentPid ${parent} -parentSessionGuid ${EDIT_SESSION} -baseUrl https://www.roblox.com -channel production`;
/** The Roblox launcher process. It carries a `-parentPid` and no task. */
const launcherBanner = (parent = 4): string =>
  `${EXE} -startEvent www.roblox.com/robloxQTStudioStartedEvent -launchIntentString {"task":"None"} -parentPid ${parent}`;

const logText = (banner: string, pid: number, session = ""): string => {
  let body =
    "2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] " +
    "All use of Roblox services must comply with Roblox's Terms of Use\n" +
    banner +
    "\n" +
    `2026-09-30T09:16:54.175Z,1.175599,1a78,6,Info [FLog::UIThreadNotifier] ` +
    `Constructing UIThreadNotifier for process '${pid}' with id 'https://www.roblox.com-Studio'`;
  if (session) {
    body +=
      "\n2026-09-30T09:12:05.830Z,0.830208,0edc,6,Warning [FLog::Output] " +
      `Session GUID is ${session}`;
  }
  return body + "\n";
};

/** What `parseIdentity` makes of a banner, in the shape `liveIdentities` hands over. */
const fromBanner = (banner: string, pid: number, session = "", log = ""): LogIdentity => ({
  ...parseIdentity(logText(banner, pid, session)),
  log: log || `log-${pid}`,
});

/** The measured shape: Edit 22656 -> server 19028 -> three clients. */
function fullTree(): Map<number, LogIdentity> {
  const ids = new Map<number, LogIdentity>();
  ids.set(EDIT_PID, fromBanner(editBanner(), EDIT_PID, EDIT_SESSION));
  ids.set(SERVER_PID, fromBanner(serverBanner(), SERVER_PID, SERVER_SESSION));
  for (const pid of CLIENT_PIDS) ids.set(pid, fromBanner(clientBanner(), pid, CLIENT_SESSION));
  return ids;
}

const liveSet = (ids: Map<number, LogIdentity>): Set<number> => new Set(ids.keys());

describe("parsing the parent flags", () => {
  it("reads the parent of a server and of a client", () => {
    expect(parseIdentity(logText(serverBanner(), SERVER_PID)).parent_pid).toBe(EDIT_PID);
    expect(parseIdentity(logText(clientBanner(), CLIENT_PIDS[0])).parent_pid).toBe(SERVER_PID);
  });

  it("reports no parent flag as null", () => {
    // Not zero: zero is a real pid shape, and a caller would walk to a process that
    // does not exist.
    const got = parseIdentity(logText(editBanner(), EDIT_PID));
    expect(got.parent_pid).toBeNull();
    expect(got.parent_session_guid).toBeNull();
  });

  it("reads the parent guid alongside the pid", () => {
    // Both flags sit on the same line and neither shadows the other. They were added
    // together for the File > New child, so a change that keeps one and loses the
    // other would be invisible from the pid side.
    const got = parseIdentity(logText(fileNewBanner(), 4444));
    expect(got.parent_pid).toBe(EDIT_PID);
    expect(got.parent_session_guid).toBe(EDIT_SESSION);
  });

  it("treats a non-numeric parent as absent, not zero", () => {
    const got = parseIdentity(
      logText(`${EXE} -task StartServer -localProjectFile ${PROJECT} -parentPid unknown`, 7777),
    );
    expect(got.parent_pid).toBeNull();
  });

  it("does not treat the launcher as a play-test member", () => {
    // The launcher carries a `-parentPid` and `{"task":"None"}`. Read as a play-test
    // member it would sit in the candidate pool for an unnamed Studio while being
    // nothing of the sort.
    const got = parseIdentity(logText(launcherBanner(), 999));
    expect(got.parent_pid).toBe(4);
    expect(isPlaytestTask(got.task)).toBe(false);
  });

  it("matches tasks case-insensitively and never throws", () => {
    const cases: Array<[string | null, boolean]> = [
      ["StartServer", true],
      ["startclient", true],
      ["STARTSERVER", true],
      ["EditPlace", false],
      ["EditFile", false],
      ["None", false],
      [null, false],
    ];
    for (const [task, expected] of cases) {
      expect(isPlaytestTask(task), JSON.stringify(task)).toBe(expected);
    }
  });
});

describe("the play-test tree", () => {
  it("only session members are children", () => {
    // Four processes carry the edit task or none; one is the root. The play test's
    // membership is what the task records, and the task is the only thing that says
    // so.
    const ids = fullTree();
    expect(playtestChildren(ids, liveSet(ids)).map((r) => r.pid)).toEqual([
      SERVER_PID,
      ...CLIENT_PIDS,
    ]);
  });

  it("anchors every child to a live parent", () => {
    const ids = fullTree();
    const live = liveSet(ids);
    for (const row of playtestChildren(ids, live)) {
      expect(row.anchored, row.anchor_note).toBe(true);
      expect(live.has(row.parent_pid as number)).toBe(true);
    }
  });

  it("identifies the parent through the log pid join", () => {
    // Nothing new identifies the parent: it is a row in the same identity map, which
    // exists because the log stated that process's own PID. So the child's place comes
    // from a log line, not from a search.
    const ids = fullTree();
    const server = playtestChildren(ids, liveSet(ids)).find((r) => r.pid === SERVER_PID)!;
    expect(server.parent_place_path).toBe(PROJECT);
    expect(server.parent_task).toBe("EditFile");
    expect(server.parent_log).toBe(`log-${EDIT_PID}`);
  });

  it("reaches the Studio that pressed play", () => {
    // One hop reaches the server, which is a session with no document of its own. Two
    // reach the Edit Studio, which is the only one with a place name - so the chain is
    // what turns "some unnamed Studio" into a statement about a specific place.
    const ids = fullTree();
    const client = playtestChildren(ids, liveSet(ids)).find((r) => r.pid === CLIENT_PIDS[0])!;
    expect(client.ancestors).toEqual([SERVER_PID, EDIT_PID]);
  });

  it("terminates on a mutual parent claim", () => {
    // Two logs each naming the other as parent would otherwise loop forever, and a
    // loop inside a resolver is a hang rather than an error - the tool call would sit
    // until the client timeout with no message at all.
    const ids = new Map<number, LogIdentity>([
      [100, fromBanner(serverBanner(200), 100)],
      [200, fromBanner(clientBanner(100), 200)],
    ]);
    const live = new Set([100, 200]);
    expect(ancestorChain(100, ids, live)).toEqual([200]);
    expect(ancestorChain(200, ids, live)).toEqual([100]);
  });

  it("terminates on a self parent claim", () => {
    const ids = new Map<number, LogIdentity>([[100, fromBanner(serverBanner(100), 100)]]);
    expect(ancestorChain(100, ids, new Set([100]))).toEqual([]);
  });

  it("never makes a File > New child a candidate", () => {
    // It has a `-parentPid` and it did open a place, so keying on the pid edge alone
    // would put a *named* Studio in the pool for an unnamed one - exactly the
    // confusion the name join exists to prevent.
    const ids = new Map<number, LogIdentity>([
      [EDIT_PID, fromBanner(editBanner(), EDIT_PID)],
      [4444, fromBanner(fileNewBanner(), 4444)],
    ]);
    expect(playtestChildren(ids, new Set(ids.keys()))).toEqual([]);
  });

  it("returns rows in pid order", () => {
    // Determinism is what lets a caller read a reason that names pids.
    const ids = new Map<number, LogIdentity>(
      [300, 100, 200].map((pid) => [pid, fromBanner(clientBanner(SERVER_PID), pid)] as const),
    );
    expect(playtestChildren(ids, new Set(ids.keys())).map((r) => r.pid)).toEqual([100, 200, 300]);
  });
});

describe("resolving an unnamed row", () => {
  /** The Edit Studio and its server, with the client gone: one anchored child, one unnamed row. */
  const onlyServerTree = (): [Map<number, LogIdentity>, Set<number>] => {
    const tree = fullTree();
    const ids = new Map<number, LogIdentity>([
      [EDIT_PID, tree.get(EDIT_PID) as LogIdentity],
      [SERVER_PID, tree.get(SERVER_PID) as LogIdentity],
    ]);
    return [ids, new Set(ids.keys())];
  };

  it("resolves a lone server", () => {
    // The 1:1 case: the Edit Studio is running, one play-test process is alive, and
    // the mesh holds one unnamed row. The counts account for each other, so the join
    // is forced rather than chosen.
    const [ids, live] = onlyServerTree();
    const got = resolveUnnamedStudio(ids, live, 1);
    expect(got.resolved).toBe(true);
    if (!got.resolved) return;
    expect(got.child.pid).toBe(SERVER_PID);
    expect(got.reason).toBeNull();
  });

  it("does not resolve a client alongside its server", () => {
    // The ordinary play test, and the honest limit of the whole idea: a server and
    // its client are two processes and two unnamed mesh rows, and no log field says
    // which row is which. Refusing here is the point - the alternative is a wrong PID
    // into a function whose job is to kill that process.
    const tree = fullTree();
    const ids = new Map<number, LogIdentity>([
      [EDIT_PID, tree.get(EDIT_PID) as LogIdentity],
      [SERVER_PID, tree.get(SERVER_PID) as LogIdentity],
      [CLIENT_PIDS[0], tree.get(CLIENT_PIDS[0]) as LogIdentity],
    ]);
    const got = resolveUnnamedStudio(ids, new Set(ids.keys()), 2);
    expect(got.resolved).toBe(false);
    expect([...got.candidates].sort((a, b) => a - b)).toEqual([SERVER_PID, CLIENT_PIDS[0]]);
  });

  it("carries the place the answer belongs to", () => {
    // The child has no name, so the only thing worth reporting is which document it
    // is running - and that comes from the parent, not from it.
    const [ids, live] = onlyServerTree();
    const got = resolveUnnamedStudio(ids, live, 1);
    if (!got.resolved) throw new Error("expected a resolution");
    expect(got.child.parent_place_path).toBe(PROJECT);
    expect(got.child.parent_pid).toBe(EDIT_PID);
  });

  it("does not resolve a server with all of its clients", () => {
    // The common play test. Four anchored children, four unnamed rows, and 4! ways to
    // pair them. Nothing in any log says which row is the server.
    const ids = fullTree();
    const got = resolveUnnamedStudio(ids, liveSet(ids), 4);
    expect(got.resolved).toBe(false);
    expect(got.reason).toContain("4 connected mesh rows");
    expect(got.reason).toContain("server");
    expect([...got.candidates].sort((a, b) => a - b)).toEqual([SERVER_PID, ...CLIENT_PIDS]);
  });

  it("does not resolve two children and two rows", () => {
    // Fewer candidates is not a smaller ambiguity. This is the case a "pick the only
    // one" shortcut would get wrong, and it is a wrong kill.
    const tree = fullTree();
    const ids = new Map<number, LogIdentity>([
      [EDIT_PID, tree.get(EDIT_PID) as LogIdentity],
      [SERVER_PID, tree.get(SERVER_PID) as LogIdentity],
      [CLIENT_PIDS[0], tree.get(CLIENT_PIDS[0]) as LogIdentity],
    ]);
    const got = resolveUnnamedStudio(ids, new Set(ids.keys()), 2);
    expect(got.resolved).toBe(false);
    expect(got.reason).toContain("2 live play-test processes");
  });

  it("does not resolve a lone child among several unnamed rows", () => {
    // One play-test child, two unnamed mesh rows. The other row belongs to a Studio
    // that attached with no place open - a real signature, and one no log field
    // distinguishes. Assuming the child is the row asked about would be a guess with a
    // PID attached.
    const [ids, live] = onlyServerTree();
    const got = resolveUnnamedStudio(ids, live, 2);
    expect(got.resolved).toBe(false);
    expect(got.reason).toContain("exactly one");
    expect(got.reason).toContain("opened no place");
  });

  it("does not resolve when no row reports a name at all", () => {
    // A `name: null` row cannot exist when no row reports a name, so the mesh answer
    // and the question disagree. The count is reported rather than quietly ignored,
    // because a silent zero would let a caller read a bug upstream as a Studio that
    // cannot be identified.
    const [ids, live] = onlyServerTree();
    const got = resolveUnnamedStudio(ids, live, 0);
    expect(got.resolved).toBe(false);
    expect(got.reason).toContain("0 connected rows report no name");
    expect(got.reason).toContain("disagree");
  });

  it("still reports what the logs know when unresolved", () => {
    // An unresolved answer that carries no diagnosis forces the caller to go back to
    // square one, which is what made this case feel unfixable.
    const ids = fullTree();
    const got = resolveUnnamedStudio(ids, liveSet(ids), 4);
    expect(got.tree).toHaveLength(4);
    expect(got.tree.map((row) => row.pid).sort((a, b) => a - b)).toEqual([
      SERVER_PID,
      ...CLIENT_PIDS,
    ]);
  });
});

describe("unresolvable edges", () => {
  it("reports a missing parent flag as such", () => {
    const ids = new Map<number, LogIdentity>([
      [100, identity({ pid: 100, task: "StartClient", parent_pid: null })],
    ]);
    const got = resolveUnnamedStudio(ids, new Set([100]), 1);
    expect(got.resolved).toBe(false);
    expect(got.reason).toContain("no -parentPid");
    expect(got.candidates).toEqual([]);
  });

  it("does not anchor to a parent that is not running", () => {
    // The child names its parent, and the parent is gone. Accepting the edge anyway
    // would anchor the child to a PID that now belongs to something else, or to
    // nothing.
    const ids = new Map<number, LogIdentity>([
      [100, fromBanner(clientBanner(40000), 100)],
      [EDIT_PID, fromBanner(editBanner(), EDIT_PID)],
    ]);
    const rows = playtestChildren(ids, new Set(ids.keys()));
    expect(rows[0]?.anchored).toBe(false);
    expect(rows[0]?.anchor_note).toContain("not running");
    expect(rows[0]?.anchor_note).toContain("40000");
    const got = resolveUnnamedStudio(ids, new Set(ids.keys()), 1);
    expect(got.resolved).toBe(false);
    expect(got.candidates).toEqual([]);
  });

  it("does not anchor to a parent outside the candidate pool", () => {
    // The pool is the running Studio processes. A `-parentPid` naming anything outside
    // it - the Windows shell, a launcher already exited - is not a Studio this
    // project can place, so the child stays unanchored.
    const ids = new Map<number, LogIdentity>([[100, fromBanner(clientBanner(4), 100)]]);
    const rows = playtestChildren(ids, new Set([100]));
    expect(rows[0]?.anchored).toBe(false);
    expect(rows[0]?.anchor_note).toContain("not running");
    expect(rows[0]?.parent_place_path).toBeNull();
  });

  it("does not anchor to a running parent with no log", () => {
    // Distinct from a dead parent, and the difference matters: the process exists, so
    // a later sweep will find its log and the child will resolve. Reporting it as
    // dead would send a caller looking for a corpse.
    const ids = new Map<number, LogIdentity>([[100, fromBanner(clientBanner(200), 100)]]);
    const rows = playtestChildren(ids, new Set([100, 200]));
    expect(rows[0]?.anchored).toBe(false);
    expect(rows[0]?.anchor_note).toContain("no Studio log yet");
  });

  it("names what is missing when no play-test process exists", () => {
    const ids = new Map<number, LogIdentity>([[100, fromBanner(editBanner(), 100)]]);
    const got = resolveUnnamedStudio(ids, new Set([100]), 1);
    expect(got.resolved).toBe(false);
    expect(got.reason).toContain("StartServer");
    expect(got.reason).toContain("StartClient");
    expect(got.reason).toContain("attached with no place open");
  });

  it("refuses a self parent", () => {
    const ids = new Map<number, LogIdentity>([[100, fromBanner(serverBanner(100), 100)]]);
    const rows = playtestChildren(ids, new Set([100]));
    expect(rows[0]?.anchored).toBe(false);
    expect(rows[0]?.anchor_note).toContain("this process");
  });

  it("resolves an empty pool to nothing rather than everything", () => {
    // No logs read at all must be an unresolved answer with a reason, never a silent
    // pass that lets a caller treat "no data" as "no conflict".
    const got = resolveUnnamedStudio(new Map(), new Set(), 1);
    expect(got.resolved).toBe(false);
    expect(got.candidates).toEqual([]);
    expect(got.reason).toContain("no running Studio process");
    expect(got.reason).toContain("no place name");
  });
});

describe("the GUID cross-check", () => {
  /**
   * `-parentSessionGuid` against the parent log's `Session GUID is`.
   *
   * Two records of the same edge, so a disagreement is evidence of something. The
   * premise - that the flag holds the parent *process's* session GUID - is read off
   * the flag's name and one command line, and was never observed to hold. So the check
   * is reported and never obeyed.
   */
  const child = (stated: string | null): LogIdentity =>
    identity({
      pid: 4444,
      task: "StartClient",
      parent_pid: EDIT_PID,
      parent_session_guid: stated,
    });

  const tree = (parentSession: string | null, stated: string | null): Map<number, LogIdentity> =>
    new Map<number, LogIdentity>([
      [EDIT_PID, identity({ pid: EDIT_PID, task: "EditFile", session_guid: parentSession })],
      [4444, child(stated)],
    ]);

  it("confirms on matching guids", () => {
    const ids = tree(EDIT_SESSION, EDIT_SESSION);
    expect(playtestChildren(ids, new Set(ids.keys()))[0]?.parent_guid_confirms).toBe(true);
  });

  it("reports a mismatch as false, not omitted", () => {
    // "Checked and disagreed" and "not checked" must not look the same, or a caller
    // cannot tell a clean join from an unverified one.
    const ids = tree(SERVER_SESSION, EDIT_SESSION);
    expect(playtestChildren(ids, new Set(ids.keys()))[0]?.parent_guid_confirms).toBe(false);
  });

  it("ignores case", () => {
    // `parent_session_guid` is uppercased at parse and `session_guid` is not, so a
    // byte comparison would disagree on a log that spelled its own GUID in lower case -
    // a false alarm on a correct edge.
    const ids = tree(EDIT_SESSION.toLowerCase(), EDIT_SESSION);
    expect(playtestChildren(ids, new Set(ids.keys()))[0]?.parent_guid_confirms).toBe(true);
  });

  it("is null when missing on either side", () => {
    for (const [parentGuid, stated] of [
      [EDIT_SESSION, null],
      [null, EDIT_SESSION],
    ] as Array<[string | null, string | null]>) {
      const ids = tree(parentGuid, stated);
      expect(playtestChildren(ids, new Set(ids.keys()))[0]?.parent_guid_confirms).toBeNull();
    }
  });
});

describe("the whole chain, from real log files", () => {
  let dir = "";

  /** Edit -> server -> one client, as three log files with parseable names. */
  const writeTree = (extra: Record<string, string> = {}): void => {
    const name = (pid: number) =>
      `0.741.19.7411056_20260930T120000Z_Studio_${pid.toString(16).toUpperCase().padStart(5, "0")}_last.log`;
    writeFileSync(join(dir, name(EDIT_PID)), logText(editBanner(), EDIT_PID, EDIT_SESSION), "utf8");
    writeFileSync(join(dir, name(SERVER_PID)), logText(serverBanner(), SERVER_PID, SERVER_SESSION), "utf8");
    writeFileSync(
      join(dir, name(CLIENT_PIDS[0])),
      logText(clientBanner(), CLIENT_PIDS[0], CLIENT_SESSION),
      "utf8",
    );
    for (const [file, text] of Object.entries(extra)) {
      writeFileSync(join(dir, file), text, "utf8");
    }
  };

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "logid-parent-"));
    setLogDirectory(() => dir);
  });

  afterEach(() => {
    setLogDirectory(null);
    rmSync(dir, { recursive: true, force: true });
  });

  it("resolves from files on disk", () => {
    // parse -> identity map -> parent walk, on real files in a real directory rather
    // than on a map a test wrote.
    writeTree();
    const live = [EDIT_PID, SERVER_PID];
    const ids = liveIdentities(live);
    const got = resolveUnnamedStudio(ids, new Set(live), 1);
    expect(got.resolved).toBe(true);
    if (!got.resolved) return;
    expect(got.child.pid).toBe(SERVER_PID);
    expect(got.child.parent_pid).toBe(EDIT_PID);
    expect(got.child.parent_place_path).toBe(PROJECT);
    // The cross-check is unavailable here, not clean. `-parentSessionGuid` was only
    // ever observed on the File > New child, so a play-test banner carrying it is a
    // fixture this project has not seen. "null" and "true" have to stay
    // distinguishable, so the honest value is asserted.
    expect(got.child.parent_guid_confirms).toBeNull();
  });

  it("still refuses from files when a live client is present", () => {
    // The refusal is a property of the data, not of the test fixture, so it has to
    // hold when the identities come off disk rather than out of a map.
    writeTree();
    const live = [EDIT_PID, SERVER_PID, CLIENT_PIDS[0]];
    const got = resolveUnnamedStudio(liveIdentities(live), new Set(live), 2);
    expect(got.resolved).toBe(false);
    expect([...got.candidates].sort((a, b) => a - b)).toEqual([SERVER_PID, CLIENT_PIDS[0]]);
  });

  it("lets a log with no pid line anchor nothing", () => {
    // Measured: a Studio that dies in its first 0.36 s writes a 1,335-byte log with no
    // notifier line. It has no PID, so the sweep cannot place it - and the failure
    // mode to avoid is attributing it to some *other* process, which would put a pid
    // that does not exist into a kill.
    writeTree({
      "0.741.19.7411056_20260930T120000Z_Studio_0105A_last.log": serverBanner(),
    });
    const live = [EDIT_PID, SERVER_PID, 4242];
    const ids = liveIdentities(live);
    const got = resolveUnnamedStudio(ids, new Set(live), 1);
    expect(ids.has(4242)).toBe(false);
    expect(got.candidates).toEqual([SERVER_PID]);
  });

  it("costs time, not an identity, when the window excludes everything", () => {
    // A window wrong by a day would otherwise drop every child on a machine that has
    // been up a while, and the resolver would report "no Studio" for a Studio that is
    // running.
    writeTree();
    const live = [EDIT_PID, SERVER_PID];
    const stale = new Map(live.map((pid) => [pid, 0]));
    const got = resolveUnnamedStudio(liveIdentities(live, stale), new Set(live), 1);
    expect(got.resolved).toBe(true);
  });
});
