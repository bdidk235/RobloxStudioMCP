/**
 * Extended Roblox Studio MCP — write/update-like wrappers.
 *
 * This subpackage exposes:
 * - `writeLikeMultiEdit` (full-body replace with status return)
 * - `updateLikeMultiEdit` (batch edits with graceful skipping + warnings)
 * - `executeLuauFromFile` (run Luau source from a local file)
 *
 * @example
 * ```ts
 * import { RobloxStudio, writeLikeMultiEdit } from "roblox-studio-mcp-node/extended";
 *
 * const studio = await RobloxStudio.connect();
 * try {
 *   const status = await writeLikeMultiEdit(studio, "game.ServerScriptService.MyScript", "print('hello')", { createIfMissing: true });
 *   // status is one of: "wrote" | "unchanged" | "created"
 * } finally {
 *   await studio.close();
 * }
 * ```
 */

export { RobloxStudio } from "../roblox.js";
export {
  writeLikeMultiEdit,
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
export { updateLikeMultiEdit, UpdateResult } from "./updater.js";
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
