/**
 * The lock-file join. Ported from `python/tests/test_locks.py`.
 *
 * `locks.py` claims a `studio_id` -> PID join that needs no log read at all:
 *
 * ```
 * studio_id -> mesh name -> <mesh name>.lock -> first field
 * ```
 *
 * The strings below are the real contents of two locks on the measuring machine:
 *
 * ```
 * 84633881964039_AutoRecovery_0.rbxl.lock    -> 18896 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-... |  |
 * Template_95206881_AutoRecovery_3.rbxl.lock -> 12324 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-... |  |
 * ```
 *
 * The first named a process that had already exited - the stale-lock trap, which is
 * the one way this can return a confidently wrong PID.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  lockNameFor,
  liveLocks,
  namesFromLocks,
  parseLock,
  pidFromLock,
  readLock,
  setAutosavesDir,
} from "../src/extended/locks.js";

/** Real contents, trailing empty fields and all. */
const LOCK_12324 =
  "12324 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-7802-46dd-a529-5a5b57dacc35 |  | \n";
const LOCK_18896 =
  "18896 | RobloxStudioBeta | DESKTOP-IH0RL4D | 6cc6718a-7802-46dd-a529-5a5b57dacc35 |  | \n";

/**
 * The shape the docstring claims to tolerate: the document claimed by another user
 * puts something other than the pid in field 0.
 */
const LOCK_OTHER_USER_FIRST =
  "DOMAIN\\someone | RobloxStudioBeta | WORKSTATION | 6cc6718a-7802-46dd-a529-5a5b57dacc35 | 77 | \n";

describe("parseLock", () => {
  it("reads the measured line", () => {
    const got = parseLock(LOCK_12324);
    expect(got).not.toBeNull();
    expect(got!.pid).toBe(12324);
    expect(got!.process).toBe("RobloxStudioBeta");
    expect(got!.machine).toBe("DESKTOP-IH0RL4D");
    expect(got!.session).toBe("6cc6718a-7802-46dd-a529-5a5b57dacc35");
  });

  it("trailing empty fields do not shift anything", () => {
    // The real line ends in `|  | `, so a naive split produces empty trailing
    // fields. A parser that indexed them would return empty strings for the fields
    // it claims to read.
    const got = parseLock(LOCK_12324)!;
    expect(got.process).toBeTruthy();
    expect(got.machine).toBeTruthy();
    expect(got.session).toBeTruthy();
  });

  it("finds the pid when it is not the first field", () => {
    expect(parseLock(LOCK_OTHER_USER_FIRST)!.pid).toBe(77);
  });

  it("garbage is null not a guess", () => {
    for (const text of ["", "   \n", "no digits at all here\n", "\u0000\u0001"]) {
      expect(parseLock(text), JSON.stringify(text)).toBeNull();
    }
  });

  it("handles CRLF", () => {
    // Python reached the first line by replacing *every* `\r` with `\n` and taking
    // the first piece, which is the same as splitting on the first `\r` or `\n` -
    // so a lone CR is a line break here too, not just CRLF.
    const got = parseLock("12324 | RobloxStudioBeta | HOST | guid |  | \r\n")!;
    expect(got.pid).toBe(12324);
    expect(got.process).toBe("RobloxStudioBeta");
  });

  it("reports missing trailing fields as null rather than empty strings", () => {
    const got = parseLock("12324 | RobloxStudioBeta\n")!;
    expect(got.pid).toBe(12324);
    expect(got.process).toBe("RobloxStudioBeta");
    expect(got.machine).toBeNull();
    expect(got.session).toBeNull();
  });

  it("reads all four fields from the newline-separated layout real locks use", () => {
    // Measured, byte for byte, from the two lock files on this machine. The old
    // behaviour reported the last three fields null on this shape, because the
    // first line split on "|" yields a single field - a silent capability loss
    // (the session GUID is the second witness for a log-derived identity).
    // Fixed 2026-09-30 as a paired edit: one field per line, pipe split kept
    // as the fallback, so the pipe fixtures above still pass.
    const real =
      "12324\nRobloxStudioBeta\nDESKTOP-IH0RL4D\n6cc6718a-7802-46dd-a529-5a5b57dacc35\n\n";
    const got = parseLock(real)!;
    expect(got.pid).toBe(12324);
    expect(got.process).toBe("RobloxStudioBeta");
    expect(got.machine).toBe("DESKTOP-IH0RL4D");
    expect(got.session).toBe("6cc6718a-7802-46dd-a529-5a5b57dacc35");
  });

  it("reads the newline shape with CRLF", () => {
    const got = parseLock("12324\r\nRobloxStudioBeta\r\nHOST\r\nguid\r\n")!;
    expect(got.pid).toBe(12324);
    expect(got.session).toBe("guid");
  });
});

describe("lockNameFor", () => {
  it("appends .lock to a bare name", () => {
    expect(lockNameFor("Template_95206881_AutoRecovery_3.rbxl")).toBe(
      "Template_95206881_AutoRecovery_3.rbxl.lock",
    );
  });

  it("rejects anything with a path separator", () => {
    // The mesh reports a basename. A name carrying a separator must not be joined
    // on, or a crafted place name could reach outside the lock directory.
    for (const name of ["a/b.rbxl", "..\\..\\evil.rbxl", "C:\\x.rbxl", ""]) {
      expect(lockNameFor(name), name).toBe("");
    }
  });

  it("maps a baseplate name to its lock", () => {
    expect(lockNameFor("Baseplate-555883251.rbxl")).toBe("Baseplate-555883251.rbxl.lock");
  });
});

describe("pidFromLock", () => {
  let dir = "";

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "locks-test-"));
    setAutosavesDir(() => dir);
  });

  afterEach(() => {
    // Restored before the files go, so a failure cannot leave the module pointing at
    // a deleted temp directory for the rest of the session - which reads as every
    // later lock test finding nothing.
    setAutosavesDir(null);
    rmSync(dir, { recursive: true, force: true });
  });

  const lock = (name: string, text: string) => writeFileSync(join(dir, name), text, "utf8");

  it("resolves a live owner", () => {
    lock("Template_95206881_AutoRecovery_3.rbxl.lock", LOCK_12324);
    const got = pidFromLock("Template_95206881_AutoRecovery_3.rbxl", new Set([12324, 999]));
    expect(got.resolved, JSON.stringify(got)).toBe(true);
    if (!got.resolved) return;
    expect(got.pid).toBe(12324);
    expect(got.session).toBe("6cc6718a-7802-46dd-a529-5a5b57dacc35");
  });

  it("never returns a stale lock as a match", () => {
    // The trap this exists to avoid. The lock outlives its process - measured,
    // `84633881964039_AutoRecovery_0.rbxl.lock` was still naming pid 18896 long
    // after it exited - and returning that would be a confidently wrong PID.
    lock("84633881964039_AutoRecovery_0.rbxl.lock", LOCK_18896);
    const got = pidFromLock("84633881964039_AutoRecovery_0.rbxl", new Set([12324, 999]));
    expect(got.resolved).toBe(false);
    if (got.resolved) return;
    expect(got.stale).toBe(true);
    expect(got.pid).toBe(18896);
    expect(got.error).toContain("not running");
  });

  it("without a live set, a stale lock still resolves", () => {
    // Documented behaviour, and a footgun: omitting `livePids` is how a stale lock
    // gets trusted. Pinned so the omission is visible, not so it is encouraged.
    lock("84633881964039_AutoRecovery_0.rbxl.lock", LOCK_18896);
    const got = pidFromLock("84633881964039_AutoRecovery_0.rbxl");
    expect(got.resolved).toBe(true);
    if (!got.resolved) return;
    expect(got.pid).toBe(18896);
  });

  it("says a missing lock means the document holds none", () => {
    const got = pidFromLock("Place1.rbxl", new Set([1]));
    expect(got.resolved).toBe(false);
    if (got.resolved) return;
    expect(got.error).toContain("holds no lock");
  });

  it("refuses a path-shaped name before any read", () => {
    const got = pidFromLock("..\\..\\evil.rbxl", new Set([1]));
    expect(got.resolved).toBe(false);
    if (got.resolved) return;
    expect(got.error).toContain("bare filename");
  });

  it("gives each refusal branch its own keys", () => {
    // The reason `LockResolution` is a discriminated union rather than one shape
    // with optionals. In the Python dict every key access type-checked, so
    // `result["stale"]` on a *successful* resolution raised in the caller, with no
    // error in the code that produced it.
    writeFileSync(join(dir, "Place1.rbxl.lock"), LOCK_12324, "utf8");
    const ok = pidFromLock("Place1.rbxl", new Set([12324]));
    expect("stale" in ok).toBe(false);
    const refused = pidFromLock("Place1.rbxl", new Set([999]));
    expect(refused.resolved).toBe(false);
    if (refused.resolved) return;
    expect(refused.stale).toBe(true);
    const noName = pidFromLock("a/b.rbxl", new Set([1]));
    expect("lock" in noName).toBe(false);
  });
});

describe("readLock", () => {
  let dir = "";

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "locks-read-"));
    setAutosavesDir(() => dir);
  });

  afterEach(() => {
    setAutosavesDir(null);
    rmSync(dir, { recursive: true, force: true });
  });

  it("is null for an absent file, not a throw", () => {
    // A lock being written or removed is normal; raising would fail a join over a
    // transient condition.
    expect(readLock("absent.rbxl.lock")).toBeNull();
  });

  it("is null for an empty name", () => {
    expect(readLock("")).toBeNull();
  });
});

describe("liveLocks", () => {
  let dir = "";

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "locks-live-"));
    setAutosavesDir(() => dir);
  });

  afterEach(() => {
    setAutosavesDir(null);
    rmSync(dir, { recursive: true, force: true });
  });

  const lock = (name: string, text: string) => writeFileSync(join(dir, name), text, "utf8");

  it("is live-only when a set is given", () => {
    lock("a.rbxl.lock", LOCK_12324);
    lock("b.rbxl.lock", LOCK_18896);
    expect([...liveLocks(new Set([12324])).keys()].sort((x, y) => x - y)).toEqual([12324]);
    // With the set omitted, stale locks are included - useful for diagnosing, wrong
    // for joining, which is why joining always passes the set.
    expect([...liveLocks().keys()].sort((x, y) => x - y)).toEqual([12324, 18896]);
  });

  it("returns names in the mesh shape", () => {
    // The reverse direction has to produce exactly what the mesh reports, because
    // that string is what the join matches on.
    lock("Template_95206881_AutoRecovery_3.rbxl.lock", LOCK_12324);
    expect(namesFromLocks(new Set([12324]))).toEqual([
      "Template_95206881_AutoRecovery_3.rbxl",
    ]);
  });

  it("round-trips a name to a pid", () => {
    // The whole point of the module, end to end on one name.
    lock("Template_95206881_AutoRecovery_3.rbxl.lock", LOCK_12324);
    for (const name of namesFromLocks(new Set([12324]))) {
      const got = pidFromLock(name, new Set([12324]));
      expect(got.resolved, JSON.stringify(got)).toBe(true);
      if (!got.resolved) continue;
      expect(got.pid).toBe(12324);
    }
  });

  it("treats a missing directory as empty, not an error", () => {
    setAutosavesDir(() => join(dir, "gone"));
    expect(liveLocks(new Set([1])).size).toBe(0);
    expect(namesFromLocks(new Set([1]))).toEqual([]);
  });

  it("sorts PIDs numerically, not lexicographically", () => {
    // A `Record<number, string>` would coerce the keys to strings and order 10
    // before 9, which is the one ordering a caller reading a reason sentence gets
    // wrong. Pinned so the Map is not "simplified" into an object.
    lock("a.rbxl.lock", "10 | P | M | s |  | \n");
    lock("b.rbxl.lock", "9 | P | M | s |  | \n");
    expect([...liveLocks().keys()]).toEqual([10, 9]);
  });
});
