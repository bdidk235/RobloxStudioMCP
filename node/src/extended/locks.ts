/**
 * A Studio lock file names the PID holding it. The log is not needed at all.
 *
 * Ported from `python/src/roblox_studio_mcp/extended/locks.py`.
 *
 * Found by chasing a problem that turned out not to be a problem. The join this
 * project built is `studio_id -> mesh name -> that process's log -> the log's own
 * PID line`. Every step is host-side, but the log was load-bearing. It is not:
 *
 * ```
 * %LOCALAPPDATA%\Roblox\RobloxStudio\AutoSaves\<place>.lock
 * ```
 *
 * holds one field per line: pid, process, machine, session GUID. The lock's
 * *filename* is
 * the same unique document name the mesh reports, and its *contents* are the PID
 * owning it. So the shortest correct join is:
 *
 * ```
 * studio_id -> mesh name -> <mesh name>.lock -> first field
 * ```
 *
 * Exact, no log read, and it works when the document has an AutoRecovery entry -
 * which is the case that made `studio_id -> PID` look unresolvable.
 *
 * **A caveat on the field layout, measured rather than assumed.** The docstring in
 * the Python original - and the fixture in its test - describe `|`-separated fields.
 * Both lock files on the measuring machine, read byte for byte while writing this
 * port, contain **no `|` at all**: they are newline-separated, and the two are 77
 * bytes of `12324\nRobloxStudioBeta\nDESKTOP-IH0RL4D\n<guid>\n\n`. So
 * {@link parseLock} reads one field per line and reports all four; a single
 * pipe-separated line falls back to the old split, keeping the legacy fixtures
 * honest. The session GUID is now a second record to cross-check a
 * log-derived identity - wiring it into the join is a separate step,
 * deliberately not taken here.
 *
 * **It does not separate two concurrent URI launches of one place.** Both report the
 * mesh name `Place1`, neither holds a lock, so this resolves 0 of 3 on that
 * scenario. A lock only exists for a document Studio was actually *recovering*; a URI
 * launch that opened cleanly writes one only 1 time in 16 logs. An earlier version
 * of the Python docstring claimed the join worked for URI launches in general, which
 * the measurement contradicts.
 *
 * Measured on the measuring machine: `Template_95206881_AutoRecovery_3.rbxl.lock`
 * names pid 12324, which was live and attached.
 *
 * Two traps, both real there:
 *
 * - **A lock outlives its process.** `84633881964039_AutoRecovery_0.rbxl.lock` was
 *   left behind naming pid 18896, long dead. Trusting it returns a dead PID, so
 *   every PID is checked against the live set and a lock whose owner is gone is
 *   reported stale rather than used.
 * - **Not every document holds a lock.** A Studio that opened a plain unsaved
 *   `Place1` holds none, and its mesh name carries no identity either - that name is
 *   a document *title*, shared by anything, so it is unidentifiable rather than
 *   merely hard to look up.
 *
 * This is the same place Studio uses to stop two windows writing one file, so its
 * lifetime is a deliberate signal: it exists exactly while a process owns the
 * document.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { autosavesDir as platformAutosavesDir, basename } from "./platform.js";

/**
 * Where Studio keeps its document locks. See `platform.autosavesDir`.
 */
let autosavesDirectory: () => string = platformAutosavesDir;

/** Point {@link autosavesDir} somewhere else, or restore it with `null`. */
export function setAutosavesDir(fn: (() => string) | null): void {
  autosavesDirectory = fn ?? platformAutosavesDir;
}

/** Where Studio keeps its document locks. See `platform.autosavesDir`. */
export function autosavesDir(): string {
  return autosavesDirectory();
}

/**
 * The lock Studio would hold for a mesh-reported place name.
 *
 * The mesh reports a basename and the lock is a basename plus `.lock`, so this is a
 * plain string append. Names are Studio's own, so no sanitising is needed; anything
 * containing a path separator is rejected rather than joined on - which is a
 * traversal guard, not a formality: a crafted place name must not reach outside the
 * lock directory.
 */
export function lockNameFor(placeName: string): string {
  if (!placeName || basename(placeName) !== placeName) return "";
  return `${placeName}.lock`;
}

/** What a readable lock file states. */
export interface LockInfo {
  /** The PID holding the document, matched by shape rather than by position. */
  pid: number;
  /** Field 1, or null if the line is shorter than that. */
  process: string | null;
  /** Field 2, or null. */
  machine: string | null;
  /** Field 3, or null. */
  session: string | null;
}

/**
 * `{pid, process, machine, session}` from a lock file, or `null`.
 *
 * The lock is newline-separated in reality - one field per line: pid, process,
 * machine, session GUID (measured: no `|` byte in either live file). A single
 * line containing `|` falls back to the old pipe split, keeping the legacy
 * fixtures honest. Either way the PID is matched **by shape rather than by
 * position** - a document claimed by another user puts something other than the
 * PID in field 0. The Python original states that in a comment on a `_LOCK_PID_RE`
 * constant it then never uses, because `parse_lock` scans the fields by hand;
 * that scan is what is ported, and the dead pattern is not carried across.
 *
 * Only the first line is considered. Python reaches that by replacing **every** `\r`
 * with `\n` and taking the first `\n`-separated piece, which is the same as splitting
 * on the first `\r` or `\n` anywhere - so a lone CR is a line break here too, not
 * just CRLF.
 *
 * The digit test is ASCII, where Python's `str.isdigit()` is Unicode-aware and would
 * accept e.g. a superscript. The difference is unreachable for a real lock - and
 * Python would then raise on `int()` for the one shape where it matters - so the
 * stricter test is the safe direction.
 */
export function parseLock(text: string): LockInfo | null {
  const lines = text
    .replace(/\r/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (lines.length === 0) return null;
  const first = lines[0] as string;
  // Newline-separated is the measured shape; the pipe split is the legacy
  // fallback. Python filters empty pipe fields where this side keeps them, so
  // a trailing `|` can surface as "" here and null there - unreachable for a
  // real lock either way, and the PID gate below is identical.
  const fields =
    first.indexOf("|") >= 0
      ? first.split("|").map((field) => field.trim())
      : lines.slice(0, 4);
  let pid: number | null = null;
  for (let index = 0; index < fields.length; index += 1) {
    const field = fields[index] as string;
    if (pid === null && /^\d+$/.test(field)) {
      pid = Number(field);
      if (index === 0) break; // the common shape; stop before considering later fields
    }
  }
  if (pid === null) return null;
  return {
    pid,
    process: fields.length > 1 ? (fields[1] as string) : null,
    machine: fields.length > 2 ? (fields[2] as string) : null,
    session: fields.length > 3 ? (fields[3] as string) : null,
  };
}

/**
 * Read one lock file, or `null` if absent or unreadable.
 *
 * `null` on failure is deliberate: a lock being written or removed is normal, and
 * throwing would fail a join over a transient condition. Python's `open` raises
 * `OSError` and Node's `readFileSync` throws an `Error`; same condition, different
 * shape, and the catch covers both.
 */
export function readLock(name: string): LockInfo | null {
  if (!name) return null;
  try {
    return parseLock(readFileSync(join(autosavesDir(), name), "utf8"));
  } catch {
    return null;
  }
}

/**
 * The outcome of resolving a mesh-reported place name through its lock file.
 *
 * A discriminated union on `resolved`, because the Python original returns four
 * different dicts from one function and every key access on the result type-checked
 * regardless of which one came back - so `result["stale"]` on a *successful*
 * resolution raised at runtime, in the tool's caller, with no error anywhere in the
 * code that produced it. That is the exact failure this project's `Dict[str, Any]`
 * hole enables, and it is the reason the type is written out rather than erased.
 */
export type LockResolution =
  | {
      resolved: true;
      pid: number;
      lock: string;
      session: string | null;
      machine: string | null;
    }
  | {
      resolved: false;
      error: string;
      /** The lock name, when one was formed. Absent for a rejected place name. */
      lock?: string;
      /** The PID the lock named. Present only on the stale branch. */
      pid?: number;
      /** True only when the lock named a PID outside the live set. */
      stale?: boolean;
    };

/**
 * Resolve a mesh-reported place name to a PID through its lock file.
 *
 * `livePids` is the set of running Studio PIDs. Passing it is not optional in
 * spirit: without it a **stale lock** naming a process that exited days ago answers
 * the join, which is the one way this can produce a confidently wrong PID. When the
 * set is given, a lock naming anything outside it is reported `stale` and never
 * returned as a match.
 */
export function pidFromLock(
  placeName: string,
  livePids: ReadonlySet<number> | null = null,
): LockResolution {
  const name = lockNameFor(placeName);
  if (!name) {
    return {
      resolved: false,
      error:
        `place name ${JSON.stringify(placeName)} is not a bare filename, so it ` +
        `cannot name a lock file`,
    };
  }
  const lock = readLock(name);
  if (lock === null) {
    return {
      resolved: false,
      lock: name,
      error:
        `no readable lock named ${JSON.stringify(name)}; this document holds no ` +
        `lock, which is the case for a plain unsaved document`,
    };
  }
  const pid = lock.pid;
  if (livePids !== null && !livePids.has(pid)) {
    return {
      resolved: false,
      lock: name,
      pid,
      stale: true,
      error:
        `lock ${JSON.stringify(name)} names pid ${pid}, which is not running; it is ` +
        `a leftover from a closed Studio`,
    };
  }
  return { resolved: true, pid, lock: name, session: lock.session, machine: lock.machine };
}

/**
 * PID -> lock filename, for every lock whose owner is running.
 *
 * With `livePids` omitted this includes stale locks, which is the useful behaviour
 * for *diagnosing* (a stale lock is evidence of a crash) and the wrong behaviour for
 * joining, so joining always passes the set.
 *
 * A `Map` rather than a plain object, because the keys are PIDs: an object would
 * coerce them to strings and sort them lexicographically, which is the one ordering
 * a caller reading a reason sentence will get wrong.
 */
export function liveLocks(livePids: ReadonlySet<number> | null = null): Map<number, string> {
  const directory = autosavesDir();
  const found = new Map<number, string>();
  if (!isDirectory(directory)) return found;
  for (const name of [...readdirSync(directory)].sort()) {
    if (!name.endsWith(".lock")) continue;
    const lock = readLock(name);
    if (lock === null) continue;
    const pid = lock.pid;
    if (livePids !== null && !livePids.has(pid)) continue;
    if (!found.has(pid)) found.set(pid, name);
  }
  return found;
}

/**
 * Mesh-shaped place names for every live lock, i.e. `<name>.lock` stripped.
 *
 * This is the reverse direction: given a PID, the document name the mesh would report
 * for it. It is what lets a *launch* identify its own process without waiting for the
 * mesh to attach.
 */
export function namesFromLocks(livePids: ReadonlySet<number> | null = null): string[] {
  const suffix = ".lock";
  return [...liveLocks(livePids).values()].map((name) => name.slice(0, -suffix.length));
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}
