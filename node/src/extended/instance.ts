/**
 * Studio instance launch/list/stop, ported from
 * `python/src/roblox_studio_mcp/extended/instance.py` (39 KB).
 *
 * **This is a partial port, and the gap is deliberate rather than an oversight.**
 * See `IDENTITY.md` beside this file. In short: Python resolves
 * `studio_id -> PID` by reading the PID out of that Studio's own log file
 * (`logid.py`, 33 KB - the most subtle code in the project, with a 256 KB prefix
 * read, a filename-stamp ordering rule and a session-GUID fallback). That chain
 * is **not** ported yet. Node's `action=list` therefore identifies by process
 * enumeration plus a time window, which is weaker: with two Studios open on the
 * same place it cannot say which is which.
 *
 * What *is* ported is everything that does not depend on logid, and the launch
 * URI, whose shape is measured and load-bearing:
 *
 * - The URI needs **exactly four keys**, and **both** ids. Dropping `universeId`
 *   looks like it works - the process starts and attaches - but Studio comes up
 *   with no place open, which only shows when you ask it for a name.
 * - `universeId:0` is the value that fetches. The real universe id is not
 *   required; 0 is what File > New sends.
 * - The URI launch is dispatched to the registered protocol handler, never via
 *   `execFile`, which raises `FileNotFoundError` on a URI because a URI is not
 *   an executable path.
 */

import { execFileSync, spawn } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { basename, delimiter, join, sep } from "node:path";

import {
  autosaveDirs,
  attachedPids,
  isWindows,
  MESH_PORT,
  pidAlive,
  placeFromCommandLine,
  processRows,
  roleFromCommandLine,
  studioExe,
  terminate,
} from "./platform.js";

export const ROLE_EDIT = "edit";
export const ROLE_SERVER = "server";
export const ROLE_CLIENT = "client";
export const ROLE_UNKNOWN = "unknown";

/**
 * How long a launch waits for the new Studio to attach to the mesh. A constant
 * because it describes Studio's behaviour, not the caller's intent. Measured: a
 * fresh instance on an already-warm place joins in roughly 20-25 s, so 75 s is
 * generous without approaching the 120 s client timeout.
 */
export const LAUNCH_WAIT = 75.0;

/**
 * The universe id that actually fetches. Provenance kept apart on purpose:
 * **key required** is user-confirmed experience; **value 0** is measured here;
 * **key count of four** is measured here.
 */
export const URI_UNIVERSE_ID = 0;

/** Unauthenticated endpoint mapping a place to its universe. */
export const UNIVERSE_API =
  "https://apis.roblox.com/universes/v1/places/{placeId}/universe";

/** A place's universe could not be resolved. Carries how many attempts ran. */
export class UniverseLookupError extends Error {
  readonly placeId: number;
  readonly attempts: number;
  readonly reason: string;

  constructor(placeId: number, attempts: number, reason: string) {
    super(
      `could not resolve a universe id for place ${placeId} after ` +
        `${attempts} attempt(s): ${reason}`,
    );
    this.name = "UniverseLookupError";
    this.placeId = placeId;
    this.attempts = attempts;
    this.reason = reason;
  }
}

/** Injectable for tests; defaults to the platform `fetch`. */
export type FetchLike = (url: string) => Promise<{
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}>;

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Ask Roblox which universe a place belongs to.
 *
 * Unauthenticated: `GET {UNIVERSE_API}` returns `{universeId: N}`. Measured for
 * the baseplate template `95206881`, which returns `28220420` — a value that
 * opens the place, 8 launches out of 8.
 *
 * `retries` is 3 by default because a single fast call is the failure mode
 * worth designing against: this runs on a launch path, so a transient network
 * blip would otherwise surface as "could not open the place" and send the caller
 * looking at the URI rather than at the network. Each retry waits `delay`
 * milliseconds, doubling, so three attempts span roughly 1.5s.
 *
 * This is deliberately **not** inlined into {@link buildLaunchUri}'s signature
 * path for callers who want a pure builder — see that function for why the
 * default is `null` rather than a number.
 */
export async function resolveUniverseId(
  placeId: number,
  opts: { retries?: number; delayMs?: number; fetchImpl?: FetchLike } = {},
): Promise<number> {
  const retries = Math.max(1, opts.retries ?? 3);
  const delayMs = opts.delayMs ?? 500;
  const doFetch: FetchLike = opts.fetchImpl ?? ((url) => fetch(url));
  const url = UNIVERSE_API.replace("{placeId}", String(Math.trunc(placeId)));

  let reason = "no attempt made";
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const res = await doFetch(url);
      if (!res.ok) {
        reason = `HTTP ${res.status}`;
      } else {
        const payload = (await res.json()) as { universeId?: unknown };
        // A response with no universeId must NOT become 0. Coercing it would
        // silently mean "no universe context" — the exact substitution this
        // derivation exists to make impossible.
        if (payload?.universeId === undefined || payload?.universeId === null) {
          reason = `response carried no universeId: ${JSON.stringify(payload)}`;
        } else {
          return Math.trunc(Number(payload.universeId));
        }
      }
    } catch (err) {
      reason = err instanceof Error ? err.message : String(err);
    }
    if (attempt < retries) await sleep(delayMs * 2 ** (attempt - 1));
  }
  throw new UniverseLookupError(Math.trunc(placeId), retries, reason);
}

/**
 * Build the `roblox-studio:` URI for a place, deriving its universe.
 *
 * **A place's universe is not an independent parameter — it is a function of the
 * place id.** So `universeId` defaults to `null`, which means *ask*, and the
 * answer comes from {@link resolveUniverseId} rather than from the caller. That
 * is the point of the default: a caller cannot supply a universe id that
 * disagrees with the place, because the common case never supplies one.
 *
 * Pass `universeId` explicitly only when you know a different universe is wanted
 * — notably {@link URI_UNIVERSE_ID} (0) to say "this place, no universe context",
 * which is what a template is and what Studio's own *File > New* emits. Both
 * values are measured to open the place: 8 of 8 on the real id, 6 of 6 on 0.
 *
 * **Async because the default performs I/O.** It used to be a required argument
 * on the reasoning that a default is "a default in disguise"; that had the
 * causality backwards, and the deeper problem was structural — an independent
 * `universeId` is a value the caller must already know, and what people supply
 * by reflex is whatever their last place's universe happened to be.
 *
 * Mirrors the Python `build_launch_uri`; see `node/src/extended/IDENTITY.md` for
 * where the two sides are deliberately *not* equivalent.
 */
export async function buildLaunchUri(
  placeId: number,
  universeId: number | null = null,
  opts: { retries?: number; delayMs?: number; fetchImpl?: FetchLike } = {},
): Promise<string> {
  const uid = universeId === null
    ? await resolveUniverseId(placeId, opts)
    : universeId;
  return (
    `roblox-studio:1+task:EditPlace` +
    `+placeId:${Math.trunc(placeId)}` +
    `+universeId:${Math.trunc(uid)}`
  );
}

/** Hand the URI to the registered protocol handler. */
export async function launchViaUri(
  placeId: number,
  universeId: number | null = null,
  opts: { retries?: number; delayMs?: number; fetchImpl?: FetchLike } = {},
): Promise<void> {
  const uri = await buildLaunchUri(placeId, universeId, opts);
  if (isWindows()) {
    // `start "" "<uri>"` rather than execFile: a protocol URI is not an
    // executable path, and spawn() on one raises ENOENT.
    execFileSync("cmd", ["/c", "start", "", uri], { stdio: "ignore" });
  } else {
    execFileSync("open", [uri], { stdio: "ignore" });
  }
}

/** Start Studio with a file and return the child handle. */
export function launchViaFile(exe: string, args: string[]): number | null {
  const child = spawn(exe, args, {
    cwd: exe.slice(0, Math.max(exe.lastIndexOf(sep), exe.lastIndexOf("/"))) || undefined,
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  return child.pid ?? null;
}

// --- places ----------------------------------------------------------------

/**
 * `Template_<placeId>_AutoRecovery_<n>[_<stamp>].rbxl` - Roblox's template
 * autosave naming, which carries the place id in the filename. That is the place a
 * template *is*, and it is what a URI launch needs: a local file has no place id,
 * so without this the only way to open a template by id is to hardcode one.
 */
const TEMPLATE_NAME_RE = /^Template_(\d+)_AutoRecovery_/;

/**
 * The place id a Roblox template autosave belongs to, or null.
 *
 * Measured: the baseplate this machine discovers is
 * `Template_95206881_AutoRecovery_4_20260930_135756.rbxl`, so the template is
 * place `95206881`. With {@link buildLaunchUri} fixed to `universeId:0`, that id
 * plus 0 opens the template by URI.
 */
export function templatePlaceId(path: string): number | null {
  const match = TEMPLATE_NAME_RE.exec(basename(path));
  return match ? Number(match[1]) : null;
}

export interface PlaceCandidate {
  path: string;
  name: string;
  place_id: number | null;
  modified: number;
}

/** Discovered places, newest first. */
export function listPlaceCandidates(): PlaceCandidate[] {
  const out: PlaceCandidate[] = [];
  for (const dir of autosaveDirs()) {
    if (!existsSync(dir)) continue;
    let names: string[];
    try {
      names = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!/\.rbxl$/i.test(name)) continue;
      if (/\.lock$/i.test(name)) continue;
      const full = join(dir, name);
      try {
        if (!statSync(full).isFile()) continue;
        out.push({
          path: full,
          name,
          place_id: templatePlaceId(name),
          modified: statSync(full).mtimeMs,
        });
      } catch {
        // Raced with Roblox pruning. Skip rather than fail the whole listing.
      }
    }
  }
  out.sort((a, b) => b.modified - a.modified);
  return out;
}

/**
 * The newest discovered place, or null.
 *
 * Honours `$ROBLOX_STUDIO_BASEPLATE` first so a caller can name a real place.
 * Otherwise globs Roblox's template autosaves: their filenames carry a rotating
 * suffix and Roblox prunes them, so pinning an exact name is fragile.
 */
export function findBaseplate(): string | null {
  const override = (process.env["ROBLOX_STUDIO_BASEPLATE"] ?? "").trim();
  if (override) {
    return existsSync(override) ? override : null;
  }
  const rows = listPlaceCandidates();
  return rows.length > 0 ? rows[0]!.path : null;
}

// --- list / stop -----------------------------------------------------------

export interface StudioProcess {
  pid: number;
  role: string;
  attached_to_mesh: boolean | null;
  place_file: string | null;
  created: string;
}

/** Every running Studio, with its role and mesh attachment. */
export function listStudioProcesses(withAttachment = true): StudioProcess[] {
  const attached = new Set(withAttachment ? attachedPids() : []);
  const out = processRows().map((row) => ({
    pid: row.pid,
    role: roleFromCommandLine(row.command_line),
    attached_to_mesh: withAttachment ? attached.has(row.pid) : null,
    place_file: placeFromCommandLine(row.command_line),
    created: row.created,
  }));
  out.sort((a, b) => (a.created || String(a.pid)).localeCompare(b.created || String(b.pid)));
  return out;
}

export interface StopResult {
  stopped: boolean;
  pid: number;
  error?: string;
}

/**
 * Terminate a Studio and wait for it to actually exit.
 *
 * Termination is irreversible, so the wait is what makes the answer true: a
 * `Stop-Process` that returns does not mean the process is gone yet.
 */
export function stopProcess(pid: number, graceSeconds = 10): StopResult {
  terminate(pid);
  const deadline = Date.now() + graceSeconds * 1000;
  while (Date.now() < deadline) {
    if (!pidAlive(pid)) return { stopped: true, pid };
    sleepMs(500);
  }
  return {
    stopped: !pidAlive(pid),
    pid,
    error: "process did not exit within the grace period",
  };
}

function sleepMs(ms: number): void {
  // Synchronous on purpose: this is a bounded grace loop at the end of a tool
  // call, and an async version would need the whole call chain to be async for
  // no benefit. The wait is capped at 10 s.
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/**
 * The Studio executable, or null.
 *
 * A thin named wrapper so the call site reads as intent rather than as a
 * filesystem probe, and so the "not installed" case is a value to check rather
 * than an exception to catch at three call sites.
 */
export function studioExeSafe(): string | null {
  try {
    return studioExe();
  } catch {
    return null;
  }
}

export { MESH_PORT, sep, delimiter };
