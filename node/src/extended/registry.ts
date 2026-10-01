/**
 * Host-side registry of connected Studio instances.
 *
 * Why this exists
 * ---------------
 * `list_roblox_studios` is the only discovery tool the official Studio MCP
 * offers, and its `studio_id` is **not stable**: it is minted by the proxy
 * process and changes on every Studio restart. That makes it a transport token,
 * not an identity, and it is the reason this project cannot answer questions
 * like "is the Studio I launched still the one I launched?".
 *
 * Roblox's documentation frames the explicit id as deliberate — agents address
 * instances "explicitly instead of relying on session state" — so there is no
 * documented in-band path to the id, and none exists in practice:
 *
 * - `game.UniqueId` is unreadable under `execute_luau`; the thread lacks the
 *   `RobloxScript` capability ("The current thread cannot read 'UniqueId'").
 *   `ReflectionService` does not list it either, since it is capability-gated
 *   out of the class registry.
 * - `game.JobId` is empty outside a live session.
 * - `game.Parent` is `null`.
 *
 * `game:GetDebugId()` *is* readable, and is the usable substitute: it is stable
 * across repeated calls, differs per Studio instance, and identifies the
 * DataModel root. Two limits, both load-bearing:
 *
 * - It is per **DataModel root**, not per process. A play session reports a
 *   different value than Edit mode does, so this must be read from **Edit**; a
 *   Server-side read will not match and must not be compared against an Edit read.
 * - Whether it survives a Studio restart is **unverified**. It is derived from
 *   the place instance rather than the proxy session, so it is expected to be
 *   stable, but that expectation has not been measured.
 *
 * This module keeps the join **host-side**, in this process's own state
 * directory. Nothing is written into the DataModel, so a registry entry can
 * never reach the place file, a published place, or a team create.
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { RobloxStudio } from "../roblox.js";
import type { CallToolResult } from "../types.js";

/** Bumped when the on-disk shape changes; older files are ignored, not deleted. */
export const REGISTRY_VERSION = 1;

/**
 * Entries not seen for this long are reported as stale rather than dropped, so a
 * closed Studio stays discoverable as "was here" without looking live.
 */
export const STALE_AFTER_SECONDS = 24 * 60 * 60;

/**
 * Entries unseen for this long are dropped on the next record, bounding growth.
 * Every restart orphans its old entry permanently - a new debug_id means a new
 * key the old one can never match again - so without pruning the file grows by
 * one entry per restart forever. Thirty days keeps the "was here" history that
 * resolve() is for while dropping what no caller will ask about. The
 * just-recorded entry is exempt, so clock skew can only spare entries, never
 * delete the one just written.
 */
export const EXPIRE_AFTER_SECONDS = 30 * 24 * 60 * 60;

/**
 * Luau that reads the in-band identity. Prints rather than returns: inline
 * execute_luau drops a script's return value, so the console is the only
 * channel back.
 */
const IDENTITY_EXPR = [
  '{',
  '\tgame:GetDebugId(),',
  '\ttostring(game.Name),',
  '\ttostring(game.PlaceId),',
  '\ttostring(game.GameId),',
  '}',
].join("\n");

/**
 * Primary channel. One call, and no console write.
 *
 * Returns a **concatenated string**, never a table: a table arrives with its
 * integer keys stringified (`{"1":…}`) and a string has no keys to lose. The
 * payload is a few bytes against a 100,015-character limit.
 */
const READ_IDENTITY_RETURN_LUAU = `return "MCPID\\t" .. table.concat(${IDENTITY_EXPR}, "\\t")`;

/**
 * Fallback only. `print` is not primary because it writes into a user-visible
 * buffer and leaves a tag behind; it is the fallback because it is the only
 * channel known to work on every build seen.
 */
const READ_IDENTITY_PRINT_LUAU = `print("MCPID\\t" .. table.concat(${IDENTITY_EXPR}, "\\t"))`;

const IDENTITY_PREFIX = "MCPID\t";

export interface RegistryEntry {
  debug_id: string | null;
  name: string | null;
  place_id: number | null;
  game_id: number | null;
  last_studio_id: string;
  last_seen: number;
  studio_id_history?: string[];
  id_changed?: boolean;
}

export interface RegistryFile {
  version: number;
  instances: Record<string, RegistryEntry>;
}

export interface InBandIdentity {
  debug_id: string;
  name: string;
  place_id: number;
  game_id: number;
}

/**
 * Return the registry file path for this host.
 *
 * `ROBLOX_STUDIO_MCP_REGISTRY` overrides it, which is what the tests use.
 */
export function registryPath(): string {
  const override = process.env["ROBLOX_STUDIO_MCP_REGISTRY"];
  if (override) return override;
  if (process.platform === "win32") {
    const base = process.env["LOCALAPPDATA"] ?? join(homedir(), "AppData", "Local");
    return join(base, "roblox-studio-mcp", "studios.json");
  }
  if (process.platform === "darwin") {
    return join(homedir(), "Library", "Application Support", "roblox-studio-mcp", "studios.json");
  }
  const base = process.env["XDG_STATE_HOME"] ?? join(homedir(), ".local", "state");
  return join(base, "roblox-studio-mcp", "studios.json");
}

function loadInternal(path: string): RegistryFile {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      (parsed as RegistryFile).version !== REGISTRY_VERSION
    ) {
      // A file from a different version is left alone: another tool version may
      // own it, and deleting it would destroy their state.
      return { version: REGISTRY_VERSION, instances: {} };
    }
    const file = parsed as RegistryFile;
    if (file.instances === null || typeof file.instances !== "object") {
      return { version: REGISTRY_VERSION, instances: {} };
    }
    return file;
  } catch {
    return { version: REGISTRY_VERSION, instances: {} };
  }
}

/** Write atomically so a crash mid-write cannot leave a truncated file. */
function store(path: string, data: RegistryFile): void {
  const directory = dirname(path) || ".";
  mkdirSync(directory, { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(temporary, `${JSON.stringify(data, null, 2)}\n`, "utf8");
    renameSync(temporary, path);
  } catch (exc) {
    if (existsSync(temporary)) {
      try {
        renameSync(temporary, `${temporary}.orphan`);
      } catch {
        /* best effort */
      }
    }
    throw exc;
  }
}

/**
 * Read the DataModel's own identity via `GetDebugId`.
 *
 * Must run in Edit mode. A play session reports a different id for the same
 * Studio, so a Server-side read is not comparable.
 */
export async function readInBandIdentity(studio: RobloxStudio): Promise<InBandIdentity | null> {
  // **The return channel is primary; `print` is the fallback.** This was
  // console-only because the code believed `execute_luau` drops a script's
  // return value — measured false on the current build and recorded as such in
  // TODO.md, but the call site kept the old belief. So the identity read paid
  // two tool calls per Studio and wrote into a user-visible buffer for a value
  // the return channel already carries. Console reads are also the fragile
  // channel: a measured case had the console reporting 0 tagged lines while the
  // log held one.
  //
  // `print` stays as the fallback because it is the only channel known to work
  // on every build seen — not because it is more reliable. So a build that
  // regresses the return channel still resolves, at the cost of a console write
  // rather than an identity.
  try {
    const result = await studio.call("execute_luau", {
      code: READ_IDENTITY_RETURN_LUAU,
      datamodel_type: "Edit",
    });
    const viaReturn = parseIdentity(result.text());
    if (viaReturn) return viaReturn;
  } catch {
    // A channel failure is not a read failure; the fallback exists for exactly
    // this case, so reporting it here would turn a recovery into an error.
  }

  await studio.call("execute_luau", {
    code: READ_IDENTITY_PRINT_LUAU,
    datamodel_type: "Edit",
  });
  const console: CallToolResult = await studio.call("get_console_output", {});
  return parseIdentity(console.text());
}

/**
 * Pull the four fields out of a tagged line, or `null` if it is not one.
 *
 * Shared by both channels deliberately: the only difference between returning
 * and printing is how the text arrives, so two copies of this would be two
 * things to keep in step.
 */
export function parseIdentity(text: string): InBandIdentity | null {
  for (const raw of text.split("\n")) {
    const line = raw.trim().replace(/^"|"$/g, "");
    // Match the tag anywhere in the line: the tool can leave its own quoting in
    // place, and a tab inside the payload is not escaped.
    const start = line.indexOf(IDENTITY_PREFIX);
    if (start < 0) continue;
    const fields = line.slice(start + IDENTITY_PREFIX.length).split("\t");
    // Four fields follow the tag: debug_id, name, place_id, game_id.
    if (fields.length < 4) continue;
    const toInt = (value: string): number => (/^\d+$/.test(value) ? Number(value) : 0);
    return {
      debug_id: fields[0]!,
      name: fields[1]!,
      place_id: toInt(fields[2]!),
      game_id: toInt(fields[3]!),
    };
  }
  return null;
}

/** Merge one observed instance into the registry and return the entry. */
export function record(options: {
  studioId: string;
  identity?: InBandIdentity | null;
  path?: string;
}): RegistryEntry {
  const target = options.path ?? registryPath();
  const data = loadInternal(target);
  const now = Date.now() / 1000;

  const key = options.identity?.debug_id ?? `session:${options.studioId}`;
  const previous = data.instances[key];
  const entry: RegistryEntry = {
    debug_id: options.identity?.debug_id ?? null,
    name: options.identity?.name ?? null,
    place_id: options.identity?.place_id ?? null,
    game_id: options.identity?.game_id ?? null,
    last_studio_id: options.studioId,
    last_seen: now,
  };

  // Keep the most recent proxy ids per stable key, so a restart is visible as
  // the id changing while the entry keeps its identity.
  const history = Array.isArray(previous?.studio_id_history) ? previous!.studio_id_history! : [];
  if (!history.includes(options.studioId)) history.push(options.studioId);
  entry.studio_id_history = history.slice(-10);
  entry.id_changed = entry.studio_id_history.length > 1;

  data.instances[key] = entry;
  for (const oldKey of Object.keys(data.instances)) {
    if (oldKey === key) continue;
    const raw = Number(data.instances[oldKey]?.last_seen ?? 0);
    // A non-numeric last_seen is not an entry this module wrote; treat it as
    // infinitely old rather than immortal. Mirrors the Python fallback to 0.
    const seen = Number.isFinite(raw) ? raw : 0;
    if (now - seen > EXPIRE_AFTER_SECONDS) {
      delete data.instances[oldKey];
    }
  }
  store(target, data);
  return entry;
}

/** Return the whole registry as read from disk. */
export function loadAll(path?: string): RegistryFile {
  return loadInternal(path ?? registryPath());
}

export interface ResolveSelector {
  debugId?: string;
  name?: string;
  placeId?: number;
}

/**
 * Look a registered instance up by its stable key.
 *
 * Refuses to guess: an ambiguous match returns every candidate so the caller can
 * choose, which is the same rule the Studio-selection path uses. A stale entry
 * is returned but flagged, never treated as live.
 */
export function resolve(selector: ResolveSelector = {}, path?: string): Record<string, unknown> {
  const data = loadInternal(path ?? registryPath());
  const now = Date.now() / 1000;

  const decorate = (entry: RegistryEntry): Record<string, unknown> => ({
    ...entry,
    stale: now - (entry.last_seen ?? 0) > STALE_AFTER_SECONDS,
    age_seconds: Math.round((now - (entry.last_seen ?? 0)) * 1000) / 1000,
  });

  if (selector.debugId) {
    const hit = data.instances[selector.debugId];
    if (hit) return { status: "ok", match: decorate(hit) };
    return {
      status: "not_found",
      reason: `No registered instance with debug_id ${JSON.stringify(selector.debugId)}.`,
      known: Object.values(data.instances).map(decorate),
    };
  }

  let candidates = Object.values(data.instances);
  if (selector.name !== undefined) candidates = candidates.filter((e) => e.name === selector.name);
  if (selector.placeId !== undefined) {
    candidates = candidates.filter((e) => Number(e.place_id ?? 0) === Number(selector.placeId));
  }
  const matches = candidates.map(decorate);
  if (matches.length === 1) return { status: "ok", match: matches[0] };
  if (matches.length === 0) {
    return {
      status: "not_found",
      reason: "No registered instance matches those selectors.",
      known: Object.values(data.instances).map(decorate),
    };
  }
  return {
    status: "ambiguous",
    reason:
      `${matches.length} registered instances match. Pass debugId to pick one; ` +
      "list order is not meaningful.",
    candidates: matches,
  };
}

export interface ListInstancesOptions {
  refresh?: boolean;
  path?: string;
}

/**
 * Enumerate Studio instances, merging the proxy list with the registry.
 *
 * `studio_id` comes from the proxy and changes on every Studio restart;
 * `debug_id` comes from the DataModel and is the stable key. The registry
 * records the pairing so a restart shows up as a changed id under a stable
 * identity, which is what makes a launched instance traceable afterwards.
 */
export async function listInstances(
  studioClient?: RobloxStudio,
  options: ListInstancesOptions = {},
): Promise<Record<string, unknown>> {
  const path = options.path ?? registryPath();
  const refresh = options.refresh ?? true;
  // Own the connection for the whole walk: reading each identity is a tool
  // call, and a client closed after listing would leave every read failing with
  // "Not connected" and no identity to register.
  const client = studioClient ?? (await RobloxStudio.connect());
  try {
    const raw = await client.listStudios();
    const instances: Array<Record<string, unknown>> = [];

    for (const studio of raw) {
      const studioId = String(studio["id"] ?? studio["studio_id"] ?? studio["studioId"] ?? "");
      let identity: InBandIdentity | null = null;
      let identityError: string | null = null;
      if (refresh && studioId) {
        const scoped = new RobloxStudio(client.client, studioId);
        try {
          identity = await readInBandIdentity(scoped);
          if (identity === null) identityError = "GetDebugId returned no identity";
        } catch (exc) {
          identityError = exc instanceof Error ? exc.message : String(exc);
        }
      }
      const entry: Record<string, unknown> = {
        studio_id: studioId,
        reported_name: studio["name"] ?? null,
        debug_id: identity?.debug_id ?? null,
        place_id: identity?.place_id ?? (studio["placeId"] ?? null),
        game_id: identity?.game_id ?? null,
        identity_error: identityError,
      };
      if (refresh && studioId) {
        const recorded = record({ studioId, identity, path });
        entry["registered"] = true;
        entry["id_changed"] = recorded.id_changed ?? false;
      } else {
        entry["registered"] = false;
      }
      instances.push(entry);
    }

    return {
      version: REGISTRY_VERSION,
      registry_path: path,
      count: instances.length,
      instances,
      registered_total: Object.keys(loadInternal(path).instances).length,
    };
  } finally {
    if (studioClient === undefined) await client.close();
  }
}
