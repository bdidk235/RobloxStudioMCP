/**
 * Platform primitives, ported from
 * `python/src/roblox_studio_mcp/extended/platform.py`.
 *
 * Scoped deliberately: this carries what `extended_manage_instance` needs -
 * process enumeration, mesh attachment, termination, and the path helpers. It is
 * not yet a full port. The log-based identity chain (`logid.py`, 33 KB and the
 * most subtle code in the project) is **not** here, which is the one remaining
 * behavioural difference between the two servers. See `IDENTITY.md` in this
 * directory before relying on Node to resolve `studio_id` to a PID.
 *
 * Ported with the same Windows/macOS split and the same care, because the
 * mistakes worth avoiding here are all "it silently found nothing":
 *
 * - The Studio binary name differs per platform. A pattern requiring `.exe`
 *   matches no macOS banner, and "matched nothing" surfaces as *no identity*
 *   with no error at all.
 * - macOS paths are joined with `/` explicitly, not `path.join`, which uses the
 *   *host* separator and would produce `/Users/me\Library\Logs\Roblox` on
 *   Windows - a path that exists on neither platform.
 * - A Studio log is data about another machine's launch, so a path in it is split
 *   on **both** separators rather than the host's.
 */

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename as nodeBasename, delimiter, dirname, join, sep } from "node:path";

/** The proxy mesh port. Attached Studios hold an established connection to it. */
export const MESH_PORT = 13469;

export const STUDIO_BINARY_WINDOWS = "RobloxStudioBeta.exe";
export const STUDIO_BINARY_MACOS = "RobloxStudio";

/**
 * An executable smaller than this is a partially written or absent install. A
 * directory being replaced still exists and may still contain the `.exe` path,
 * so size is the only cheap signal that an install is not mid-update; launching
 * from one produced `STATUS_DLL_NOT_FOUND` twice.
 *
 * 100 MB, ported unchanged. A first draft of the Python module used 20 MB, which
 * would have quietly lowered a threshold set from an actual failure.
 */
export const MIN_EXE_BYTES = 100 * 1024 * 1024;

/** The single platform predicate. Everything branches positively on this. */
export function isWindows(): boolean {
  return process.platform === "win32";
}

export function home(): string {
  return homedir();
}

/**
 * The last component of a path, splitting on **both** separators.
 *
 * Not for correctness against `path.basename`, which handles both on either
 * platform. It is here so the *intent* is explicit at the call site: the
 * separator in a Studio log belongs to whichever machine wrote it, not to
 * whichever machine is reading it.
 */
export function basename(path: string): string {
  if (!path) return "";
  const normalised = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalised.includes("/") ? normalised.slice(normalised.lastIndexOf("/") + 1) : normalised;
}

/** Where Studio writes its logs. */
export function logDir(): string {
  return isWindows()
    ? join(process.env["LOCALAPPDATA"] ?? "", "Roblox", "logs")
    : `${home()}/Library/Logs/Roblox`;
}

/** Where Studio keeps recovered documents, and therefore the `.lock` files. */
export function autosavesDir(): string {
  return isWindows()
    ? join(process.env["LOCALAPPDATA"] ?? "", "Roblox", "RobloxStudio", "AutoSaves")
    : `${home()}/Library/Application Support/Roblox/RobloxStudio/AutoSaves`;
}

/** The live directory plus `Archived`. */
export function autosaveDirs(): string[] {
  const root = autosavesDir();
  return isWindows() ? [root, join(root, "Archived")] : [root, `${root}/Archived`];
}

export interface InstallEntry {
  dir: string;
  studio_bytes: number | null;
  other_exes: string[];
  is_studio_install: boolean;
}

/**
 * What is actually in `Versions\*`, and what each entry is.
 *
 * Added because `studioExe()` returning null meant three different things and
 * reported all of them as one. Measured on this machine, `Versions` holds several
 * directories and only one is Studio: another is Roblox **Player**, and another is
 * an **old Studio install emptied by an update** - created 09-27, replaced
 * 09-30, and cleaned out two minutes later, leaving only the one file it had
 * locked. So "no Studio executable here" is the *normal* state of a machine with
 * Player installed, not evidence of a half-finished update.
 *
 * Ported from `platform.describe_installs` in the Python server.
 */
export function describeInstalls(): InstallEntry[] {
  const root = join(process.env["LOCALAPPDATA"] ?? "", "Roblox", "Versions");
  if (!existsSync(root)) return [];
  const out: InstallEntry[] = [];
  for (const dir of readdirSync(root).sort()) {
    const entry: InstallEntry = {
      dir,
      studio_bytes: null,
      other_exes: [],
      is_studio_install: false,
    };
    const studio = join(root, dir, STUDIO_BINARY_WINDOWS);
    try {
      entry.studio_bytes = statSync(studio).size;
    } catch {
      // absent, which is the interesting case rather than an error
    }
    try {
      for (const other of readdirSync(join(root, dir))) {
        if (other.toLowerCase().endsWith(".exe") && other !== STUDIO_BINARY_WINDOWS) {
          entry.other_exes.push(other);
        }
      }
    } catch {
      // unreadable directory; the entry still reports what was read
    }
    entry.is_studio_install =
      entry.studio_bytes !== null && entry.studio_bytes >= MIN_EXE_BYTES;
    out.push(entry);
  }
  return out;
}

/** The Studio executable, newest complete install. */
export function studioExe(): string | null {
  if (!isWindows()) {
    for (const candidate of [
      "/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio",
      `${home()}/Applications/RobloxStudio.app/Contents/MacOS/RobloxStudio`,
    ]) {
      if (existsSync(candidate)) return candidate;
    }
    return null;
  }

  const root = join(process.env["LOCALAPPDATA"] ?? "", "Roblox", "Versions");
  const candidates: Array<[number, string]> = [];
  for (const entry of describeInstalls()) {
    if (entry.studio_bytes === null) continue;
    const path = join(root, entry.dir, STUDIO_BINARY_WINDOWS);
    try {
      candidates.push([statSync(path).mtimeMs, path]);
    } catch {
      // Replaced mid-scan; the Python port skips these too.
    }
  }
  candidates.sort((a, b) => b[0] - a[0]);
  for (const [, path] of candidates) {
    if (statSync(path).size >= MIN_EXE_BYTES) return path;
  }
  return null;
}

/** Kill a process. Blocking, and the only place in the project that kills. */
export function terminate(pid: number): void {
  if (isWindows()) {
    execFileSync(
      "powershell",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `Stop-Process -Id ${Math.trunc(pid)} -Force -ErrorAction SilentlyContinue`,
      ],
      { stdio: "ignore", timeout: 30_000 },
    );
  } else {
    execFileSync("kill", ["-9", String(Math.trunc(pid))], { stdio: "ignore", timeout: 30_000 });
  }
}

// --- processes -------------------------------------------------------------
//
// Both platforms normalise to the same shape, so nothing above this line needs to
// know which one it is on:
//
//   { pid: number, created: string, command_line: string }

function runCommand(command: string, args: string[], timeout = 30_000): string {
  try {
    return execFileSync(command, args, { encoding: "utf8", timeout, stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return "";
  }
}

function powershellJson(script: string): unknown {
  const raw = runCommand("powershell", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    `$ProgressPreference='SilentlyContinue'; ${script}`,
  ]);
  const start = Math.max(raw.indexOf("["), raw.indexOf("{"));
  if (start < 0) return null;
  try {
    return JSON.parse(raw.slice(start));
  } catch {
    return null;
  }
}

/**
 * One `ps -axo pid=,lstart=,command=` row -> a normalised process dict.
 *
 * `lstart` is **five** whitespace-separated fields (`Mon Sep 28 02:36:04 2026`),
 * so the row is split 7 ways. Splitting 3 ways put `created="Mon"`, which parses
 * as a string and silently loses the time.
 */
export function parsePsRow(line: string): { pid: number; created: string; command_line: string } | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  // Split fully, then rejoin the tail. `split(/\s+/, 7)` would *truncate* the
  // 7th field, while Python's `split(None, 6)` *keeps the remainder* - so the
  // port silently cut the command line at its first space and every macOS row
  // came back null. The difference between the two languages' `split` is the
  // whole bug; slicing and rejoining is the only faithful translation.
  const parts = trimmed.split(/\s+/);
  if (parts.length < 7 || !/^\d+$/.test(parts[0] as string)) return null;
  const pid = parts[0] as string;
  const created = parts.slice(1, 6).join(" ");
  const command = parts.slice(6).join(" ");
  if (!command.includes(STUDIO_BINARY_MACOS) && !command.includes(STUDIO_BINARY_WINDOWS)) return null;
  if (!command.includes("-task") && !command.includes("roblox-studio:")) return null;
  return { pid: Number(pid), created, command_line: command };
}

/** Every running Studio instance, normalised. */
export function processRows(): Array<{ pid: number; created: string; command_line: string }> {
  if (isWindows()) {
    const data = powershellJson(
      `Get-CimInstance Win32_Process -Filter "Name='${STUDIO_BINARY_WINDOWS}'" | ` +
        "Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress",
    );
    if (data === null) return [];
    const rows = Array.isArray(data) ? data : [data];
    return rows.map((row) => {
      const r = row as Record<string, unknown>;
      return {
        pid: Number(r["ProcessId"] ?? 0) || 0,
        // CIM datetimes serialise as /Date(ms)/, a millisecond epoch.
        created: String(r["CreationDate"] ?? ""),
        command_line: String(r["CommandLine"] ?? ""),
      };
    });
  }

  // Exit code is deliberately ignored, and that is the correct choice here.
  // `ps` exits non-zero under conditions that are not failures - and the macOS
  // research recorded a sharper one: the obvious alternative, `pgrep -fl`, **exits
  // 1 when it matches nothing**, which is the normal state of a machine with no
  // Studio running. Treating that as an error would report "could not enumerate
  // processes" for a machine that simply has none. Stdout is parsed; nothing else
  // is trusted.
  //
  // `ps` over `pgrep` is a considered difference from prior art rather than an
  // oversight. `Chrrxs/robloxstudio-mcp` picked `pgrep -fl` deliberately, because
  // the command line arrives as a clean whitespace-delimited field; it gives up
  // per-process start time entirely and substitutes a boot id
  // (`sysctl -n kern.boottime`). This module *needs* start time - identity
  // resolution matches a log's start stamp against process creation, and
  // `parsePsRow` exists specifically to parse `lstart`'s five fields - so `ps` is
  // the right primitive here and `pgrep` is not.
  //
  // Also inherited from that research: macOS has no `MainWindowTitle`, so a window
  // title cannot be a fallback for telling two Studios apart. It was never part
  // of the identity chain here - the PID comes from each process's own
  // `UIThreadNotifier` log line - but the capability gap is real and would bite
  // anyone extending this to use titles.
  const out: Array<{ pid: number; created: string; command_line: string }> = [];
  for (const line of runCommand("ps", ["-axo", "pid=,lstart=,command="]).split("\n")) {
    const parsed = parsePsRow(line);
    if (parsed) out.push(parsed);
  }
  return out;
}

/** Whether a pid is still running. */
export function pidAlive(pid: number): boolean {
  if (isWindows()) {
    const data = powershellJson(
      `Get-CimInstance Win32_Process -Filter "ProcessId=${Math.trunc(pid)}" | ` +
        "Select-Object ProcessId | ConvertTo-Json -Compress",
    );
    if (data === null) return false;
    const rows = Array.isArray(data) ? data : [data];
    return rows.some(
      (row) => Number((row as Record<string, unknown>)["ProcessId"] ?? 0) === Math.trunc(pid),
    );
  }
  try {
    process.kill(Math.trunc(pid), 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Pids holding an established connection to the mesh on `MESH_PORT`.
 *
 * Filtered to Studio processes, because the proxy and the listener are holders
 * too, and neither is a Studio.
 *
 * macOS uses `lsof`; the mesh is a loopback WebSocket, so the owning process is
 * only visible through the system's own socket table. **Unverified on macOS** -
 * no Mac was available to test on.
 *
 * Exit code ignored for the same reason as `ps` above: "nothing is holding the
 * port" is the normal state of a machine with no Studio running, and it is an
 * empty answer, not a failure.
 *
 * `-nP` matters more than it looks: without it `lsof` resolves the port through
 * `/etc/services` and does reverse lookups, so a connection to 13469 comes back
 * as a service name rather than a number. The macOS research also records a
 * widely-read report that `sudo lsof -i` stopped returning most processes on
 * Sequoia, which resolved to two causes that do not apply here - the COMMAND
 * column truncates to 9 characters (this passes `-t`, so only pids are printed
 * at all), and QUIC traffic is UDP. No `sudo` is needed for a loopback mesh where
 * both ends are this user's own processes.
 */
export function attachedPids(): number[] {
  let holders: Set<number>;
  if (isWindows()) {
    const data = powershellJson(
      `Get-NetTCPConnection -RemotePort ${MESH_PORT} -ErrorAction SilentlyContinue | ` +
        "Where-Object { $_.State -eq 'Established' } | " +
        "Select-Object -ExpandProperty OwningProcess -Unique | ConvertTo-Json -Compress",
    );
    const rows = data === null ? [] : Array.isArray(data) ? data : [data];
    holders = new Set(
      rows.filter((p) => p !== null).map((p) => Number(p)).filter((p) => Number.isFinite(p)),
    );
  } else {
    holders = new Set(
      runCommand("lsof", ["-nP", `-iTCP:${MESH_PORT}`, "-sTCP:ESTABLISHED", "-t"])
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => /^\d+$/.test(l))
        .map(Number),
    );
  }
  const studio = new Set(processRows().map((r) => r.pid));
  return [...holders].filter((p) => studio.has(p)).sort((a, b) => a - b);
}

// --- command lines ---------------------------------------------------------

/**
 * Map a command line to a role.
 *
 * Two shapes exist. A file or test launch carries `--task X`. A URI launch
 * carries the `roblox-studio:1+task:EditPlace+...` string **as the argument**,
 * with no `-task` flag at all, so a `-task`-only parse reports every
 * URI-launched Studio as `unknown`. The task is read out of the URI here.
 */
export function roleFromCommandLine(cmd: string): string {
  const text = cmd || "";
  const flag = /-{1,2}task\s+(\S+)/.exec(text);
  let task = flag ? flag[1] : undefined;
  if (!task) {
    const uri = /roblox-studio:[^+]*\+task:([A-Za-z]+)/.exec(text);
    if (uri) task = uri[1];
  }
  const lowered = (task || "").toLowerCase();
  if (lowered.startsWith("edit")) return "edit";
  if (lowered.startsWith("startserver")) return "server";
  if (lowered.startsWith("startclient")) return "client";
  return "unknown";
}

/**
 * Name the place a process has open, whichever launch route it used.
 *
 * File launches name a path, so the **basename** is what the mesh reports and
 * what a caller matches on. URI launches carry only an id and Studio names the
 * place itself, so nothing can be derived here and this returns null.
 *
 * The first port of this returned the whole normalised path, which is not what
 * Python returns and not what the mesh names a document by - so every
 * comparison against a mesh `name` failed. Caught by the mirrored test.
 */
export function placeFromCommandLine(cmd: string): string | null {
  const text = cmd || "";
  const local = /-local(?:Place|Project)File\s+"?([^"\s]+)"?/.exec(text);
  if (!local) return null;
  const path = normalisePath(local[1] as string);
  return basename(path) || path;
}

/** Make separators native so the path can be opened on this host. */
function normalisePath(value: string): string {
  const unquoted = value.replace(/^"|"$/g, "");
  return unquoted.replace(/\//g, sep);
}

// Re-exported so callers do not need a second import for path work.
export { dirname, join, delimiter, nodeBasename };
