/**
 * Extended Roblox Studio MCP — write/update-like wrappers.
 *
 * This subpackage exposes:
 * - `writeScript` (full-body replace with status return)
 * - `updateScript` (batch edits with graceful skipping + warnings)
 * - `executeLuauFromFile` (run Luau source from a local file)
 *
 * @example
 * ```ts
  * import { RobloxStudio, writeScript } from "roblox-studio-mcp/extended";
 *
 * const studio = await RobloxStudio.connect();
 * try {
 *   const status = await writeScript(studio, "game.ServerScriptService.MyScript", "print('hello')", { createIfMissing: true });
 *   // status is one of: "wrote" | "unchanged" | "created"
 * } finally {
 *   await studio.close();
 * }
 * ```
 */

export { RobloxStudio } from "../roblox.js";
export {
  writeScript,
  chunkedWrite,
  buildChunkedLua,
  stripLinePrefixes,
  pickBracketLevel,
  luaLongBracket,
  GAME_TREE_PREFIX,
  STRING_PROPERTY_SIZE_LIMIT,
  VALID_SCRIPT_CLASSES,
  MAX_CHUNKED_RETRIES,
} from "./writer.js";
export type { WriteStatus, WriteOptions } from "./writer.js";
export { updateScript, UpdateResult } from "./updater.js";
export type { EditInput, UpdateOptions } from "./updater.js";
export {
  scriptSearchAndRead,
  insertAssetFromFile,
  ConsoleWatch,
  getWatchState,
  watchOutput,
  createModuleWithDeps,
  runTests,
  executeLuauFromFile,
  isErrorLine,
} from "./extensions.js";
export type {
  ScriptSearchEntry,
  InsertAssetOptions,
  CreateModuleOptions,
  RunTestsOptions,
  ExecuteFromFileOptions,
} from "./extensions.js";
export { extendedScriptGrep } from "./grep.js";
export type { GrepHit, GrepOptions } from "./grep.js";

// Instance control and the place/universe derivation.
//
// These were unreachable from the published package: `package.json`'s `exports`
// map allows only `.` and `./extended`, so a deep import of
// `dist/extended/instance.js` is blocked, and nothing here re-exported it. That
// made `resolveUniverseId` - the function you need to reach any universe-scoped
// Roblox API - impossible to call without depending on the repo's source tree.
// It is exported here for that reason, not for tidiness.
export {
  ROLE_EDIT,
  ROLE_SERVER,
  ROLE_CLIENT,
  ROLE_UNKNOWN,
  LAUNCH_WAIT,
  URI_UNIVERSE_ID,
  UNIVERSE_API,
  UniverseLookupError,
  resolveUniverseId,
  buildLaunchUri,
  launchViaUri,
  templatePlaceId,
  listPlaceCandidates,
  findBaseplate,
  listStudioProcesses,
  stopProcess,
  studioExeSafe,
} from "./instance.js";
export type { FetchLike, PlaceCandidate, StudioProcess, StopResult } from "./instance.js";
export {
  REGISTRY_VERSION,
  STALE_AFTER_SECONDS,
  registryPath,
  readInBandIdentity,
  record,
  loadAll,
  resolve as resolveInstance,
  listInstances as listStudioInstances,
} from "./registry.js";
export type {
  RegistryEntry,
  RegistryFile,
  InBandIdentity,
  ResolveSelector,
  ListInstancesOptions,
} from "./registry.js";
export {
  CAPTURE_LUAU,
  MAX_BASE64_CHARS,
  DEFAULT_CHUNK,
  crc32,
  encodePng,
  parseHeader,
  captureRgba,
  capturePng,
} from "./capture.js";
export type { CaptureHeader, CaptureResult } from "./capture.js";
export {
  HIT_PREFIX,
  REGISTER_LUAU,
  setBreakpoint,
  removeBreakpoint,
  clearBreakpoints,
  listBreakpoints,
} from "./breakpoints.js";
export type { Breakpoint, SetBreakpointResult } from "./breakpoints.js";
export {
  SKILLS_DIRNAME,
  SKILL_SUFFIX,
  SkillError,
  findSkillsDir,
  loadSkills,
  skillIndex,
  getSkill,
  callSkill,
} from "./skills.js";
export type { Skill } from "./skills.js";
