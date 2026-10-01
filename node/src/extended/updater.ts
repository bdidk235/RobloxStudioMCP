/**
 * Update-like wrapper around `multi_edit` for game-tree scripts.
 *
 * Mirrors Claude Code Edit semantics for game-tree scripts:
 *
 * - Reads first: the current source is always fetched via `script_read`
 *   before editing (like Edit requires Read before Edit).
 * - Exact matching: `old_string` must match exactly. A missing `old_string`
 *   fails with an `old_string not found in content` error in strict mode
 *   (or is skipped with a warning by default).
 * - Uniqueness: an `old_string` matching multiple regions fails with a
 *   `Found multiple matches for old_string ... Provide more surrounding
 *   lines ... or set replace_all:true` error in strict mode (or is skipped
 *   as ambiguous by default). Set `replace_all:true` on an edit — like
 *   Edit's `replaceAll` — to replace every occurrence instead.
 * - No-op guard: `old_string` and `new_string` must be different.
 * - Sequential: edits apply in order, each operating on the result of the
 *   previous edit (like multi-edit). Batches return a summary of what was
 *   applied, skipped, and warned.
 */

import { stripLinePrefixes, GAME_TREE_PREFIX, writeScript } from "./writer.js";
import { INVALID_ARGUMENT, ToolError, describe } from "./errors.js";
import type { RobloxStudio } from "../roblox.js";

/**
 * A single string-replacement edit.
 *
 * Accepts tuple form `[oldString, newString]` or object form with either
 * snake_case (`old_string`/`new_string`/`replace_all`, the MCP wire format)
 * or camelCase (`oldString`/`newString`/`replaceAll`, the Claude Code Edit
 * format) keys. Set `replace_all`/`replaceAll` to `true` — like Edit's
 * `replaceAll` — to replace every occurrence instead of requiring exactly one.
 */
export type EditInput =
  | readonly [string, string]
  | {
      oldString: string;
      newString: string;
      replaceAll?: boolean;
      replace_all?: boolean;
    }
  | {
      old_string: string;
      new_string: string;
      replace_all?: boolean;
      replaceAll?: boolean;
    };

/** Summary of an `updateScript` call. */
export class UpdateResult {
  updated: number[] = [];
  skippedNoMatch: number[] = [];
  skippedNoOp: number[] = [];
  skippedAmbiguous: number[] = [];
  warnings: string[] = [];

  constructor(init: Partial<UpdateResult> = {}) {
    Object.assign(this, init);
  }

  toString(): string {
    const parts: string[] = [];
    if (this.updated.length > 0) {
      parts.push(`Updated edits at positions: ${this.updated}`);
    }
    if (this.skippedNoMatch.length > 0) {
      parts.push(`Skipped (no match): positions ${this.skippedNoMatch}`);
    }
    if (this.skippedNoOp.length > 0) {
      parts.push(`Skipped (no-op): positions ${this.skippedNoOp}`);
    }
    if (this.skippedAmbiguous.length > 0) {
      parts.push(`Skipped (ambiguous, multiple matches): positions ${this.skippedAmbiguous}`);
    }
    return parts.length > 0 ? parts.join("; ") : "No changes.";
  }
}

export interface UpdateOptions {
  skipMissing?: boolean;
  skipNoOps?: boolean;
}

/**
 * The strict-mode failure for one edit in a batch.
 *
 * Built in one place because the same four faults were raised twice - once in
 * the `replace_all` path and once in the batch path - and the two copies had
 * already drifted. Naming the field and the element index here is what stops
 * `edits[3].old_string` from decaying back into `Edit 3`.
 *
 * Every one of these is the caller's own edit, so every one is
 * `INVALID_ARGUMENT`.
 *
 * Notably *not* `NOT_FOUND`, which is what the old "old_string not found in
 * content" text classified to, by accident of the substring "not found". The
 * Instance and the script were both found and read successfully; it was the
 * caller's `old_string` that did not appear in the text. `NOT_FOUND` means "the
 * thing you named is not in the DataModel", and an agent branching on it would
 * go re-listing the DataModel for a script it had just read. The fix is to copy
 * the exact text back, which is a different action entirely.
 *
 * Mirrors `_edit_fault` in
 * `python/src/roblox_studio_mcp/extended/updater.py`.
 */
function editFault(index: number, field: string, problem: string, remedy: string): ToolError {
  return new ToolError(
    INVALID_ARGUMENT,
    "edits[" + index + "]" + (field ? "." + field : "") + " " + problem + " " + remedy,
  );
}

/**
 * Apply edits to a game-tree script with graceful skipping.
 *
 * Like Claude Code Edit, the current source is read first, edits apply in
 * sequence (each operating on the result of the previous edit), and every
 * edit must be valid unless skipping is enabled.
 *
 * @param studio An initialised {@link RobloxStudio} instance.
 * @param targetPath DataModel dot-path, e.g. `"game.ServerScriptService.MyScript"`.
 * @param edits A sequence of edits: `[oldString, newString]` tuples or
 * objects with `old_string`/`new_string` (plus optional `replace_all`).
 * @param options `skipMissing` (default true) skips missing/ambiguous edits
 * with a warning instead of throwing; `skipNoOps` (default true) does the
 * same for no-op edits where `old_string` equals `new_string`.
 */
export async function updateScript(
  studio: RobloxStudio,
  targetPath: string,
  edits: readonly EditInput[],
  options: UpdateOptions = {},
): Promise<UpdateResult> {
  const { skipMissing = true, skipNoOps = true } = options;
  if (!targetPath.startsWith(GAME_TREE_PREFIX)) {
    throw new ToolError(
      INVALID_ARGUMENT,
      "target_path must be a game-tree path starting with " +
        describe(GAME_TREE_PREFIX) +
        ", got " +
        describe(targetPath) +
        ". File-system paths go to write_script.",
    );
  }

  // Read current source first (like Edit requires Read before Edit).
  const result = await studio.scriptRead(targetPath);
  const currentSource = stripLinePrefixes(result.text());

  const normalized: Array<{ oldStr: string; newStr: string; replaceAll: boolean }> = edits.map((e, index) => {
    if (Array.isArray(e)) {
      return { oldStr: e[0], newStr: e[1], replaceAll: false };
    }
    if (typeof e !== "object" || e === null) {
      // Python raised here; this side cast whatever arrived to a Record and
      // read `undefined` out of it, so a malformed edit reached Studio instead
      // of being refused. Same request, one server rejected it and the other
      // silently accepted it.
      throw editFault(
        index,
        "",
        "must be an (old_string, new_string) tuple or dict,",
        "got " + describe(e) + ".",
      );
    }
    const d = e as Record<string, unknown>;
    // Key presence, not truthiness: `??` would treat an explicit
    // `new_string: null` as absent and fall through to the alias, so an
    // explicit null reached Studio as `undefined`. Python's
    // `dict.get(key, default)` only falls back on a missing key.
    const oldStr = (Object.prototype.hasOwnProperty.call(d, "old_string")
      ? d["old_string"]
      : d["oldString"]) as string;
    const newStr = (Object.prototype.hasOwnProperty.call(d, "new_string")
      ? d["new_string"]
      : d["newString"]) as string;
    const replaceAll = Boolean(d["replace_all"] ?? d["replaceAll"] ?? false);
    return { oldStr, newStr, replaceAll };
  });

  // When any edit opts into replace-all, apply the batch sequentially in
  // memory (each edit operating on the result of the previous one, like
  // Claude Code multi-edit) and write the final body once. The raw
  // multi_edit requires exactly one match per old_string, so it cannot
  // express replace-all directly.
  if (normalized.some((e) => e.replaceAll)) {
    return applySequentially(studio, targetPath, currentSource, normalized, { skipMissing, skipNoOps });
  }

  // Classify each edit against the current source.
  const validEdits: Array<{ old_string: string; new_string: string }> = [];
  const summary = new UpdateResult();

  for (let i = 0; i < normalized.length; i++) {
    const { oldStr, newStr } = normalized[i];
    // No-op guard: oldString and newString must be different.
    if (oldStr === newStr) {
      summary.skippedNoOp.push(i);
      if (skipNoOps) {
        summary.warnings.push(`Edit ${i}: old_string matches new_string — skipped as no-op.`);
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "equals new_string.",
          "Send different text, or set skip_no_ops=true to drop it.",
        );
      }
    }

    if (oldStr === "") {
      summary.skippedNoMatch.push(i);
      if (skipMissing) {
        summary.warnings.push(`Edit ${i}: old_string must not be empty — skipped.`);
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "is empty.",
          "An empty old_string has no unique match; send the text to replace.",
        );
      }
    }

    const occurrences = currentSource.split(oldStr).length - 1;
    // Check if old_string exists at all.
    if (occurrences === 0) {
      summary.skippedNoMatch.push(i);
      if (skipMissing) {
        summary.warnings.push(`Edit ${i}: old_string not found in content — skipped.`);
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "not found in content.",
          "Re-read the script and copy the text exactly - matching is exact, " +
            "including indentation and line endings.",
        );
      }
    }

    // multi_edit requires exactly one match; duplicates would fail downstream.
    if (occurrences > 1) {
      summary.skippedAmbiguous.push(i);
      if (skipMissing) {
        summary.warnings.push(
          `Edit ${i}: Found multiple matches for old_string (${occurrences} occurrences) — skipped as ambiguous. ` +
            `Provide more surrounding lines to narrow it to a unique block, or set replace_all:true to replace all occurrences.`,
        );
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "matched " + occurrences + " times; exactly 1 is required.",
          "Add surrounding lines to narrow it to a unique block, or set " +
            "replace_all=true on this edit to replace all occurrences.",
        );
      }
    }

    validEdits.push({ old_string: oldStr, new_string: newStr });
    summary.updated.push(i);
  }

  if (validEdits.length === 0) {
    summary.warnings.push("No valid edits to apply.");
    return summary;
  }

  // Apply remaining edits in a single atomic multi_edit call.
  await studio.call("multi_edit", {
    file_path: targetPath,
    datamodel_type: "Edit",
    edits: validEdits,
  });

  return summary;
}

/**
 * Apply a batch sequentially in memory, then write the final body once.
 *
 * Used when any edit sets `replace_all` (like Edit's `replaceAll`). Each
 * edit operates on the result of the previous edit. Edits without
 * `replace_all` still require exactly one match at application time.
 */
async function applySequentially(
  studio: RobloxStudio,
  targetPath: string,
  currentSource: string,
  normalized: Array<{ oldStr: string; newStr: string; replaceAll: boolean }>,
  options: { skipMissing: boolean; skipNoOps: boolean },
): Promise<UpdateResult> {
  const { skipMissing, skipNoOps } = options;
  const summary = new UpdateResult();
  let evolving = currentSource;

  for (let i = 0; i < normalized.length; i++) {
    const { oldStr, newStr, replaceAll } = normalized[i];
    if (oldStr === newStr) {
      summary.skippedNoOp.push(i);
      if (skipNoOps) {
        summary.warnings.push(`Edit ${i}: old_string matches new_string — skipped as no-op.`);
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "equals new_string.",
          "Send different text, or set skip_no_ops=true to drop it.",
        );
      }
    }

    if (oldStr === "") {
      summary.skippedNoMatch.push(i);
      if (skipMissing) {
        summary.warnings.push(`Edit ${i}: old_string must not be empty — skipped.`);
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "is empty.",
          "An empty old_string has no unique match; send the text to replace.",
        );
      }
    }

    const occurrences = evolving.split(oldStr).length - 1;
    if (occurrences === 0) {
      summary.skippedNoMatch.push(i);
      if (skipMissing) {
        summary.warnings.push(`Edit ${i}: old_string not found in content — skipped.`);
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "not found in content.",
          "Re-read the script and copy the text exactly - matching is exact, " +
            "including indentation and line endings.",
        );
      }
    }

    if (occurrences > 1 && !replaceAll) {
      summary.skippedAmbiguous.push(i);
      if (skipMissing) {
        summary.warnings.push(
          `Edit ${i}: Found multiple matches for old_string (${occurrences} occurrences) — skipped as ambiguous. ` +
            `Provide more surrounding lines to narrow it to a unique block, or set replace_all:true to replace all occurrences.`,
        );
        continue;
      } else {
        throw editFault(
          i,
          "old_string",
          "matched " + occurrences + " times; exactly 1 is required.",
          "Add surrounding lines to narrow it to a unique block, or set " +
            "replace_all=true on this edit to replace all occurrences.",
        );
      }
    }

    evolving = replaceAll ? evolving.split(oldStr).join(newStr) : evolving.replace(oldStr, newStr);
    summary.updated.push(i);
  }

  if (summary.updated.length === 0) {
    summary.warnings.push("No valid edits to apply.");
    return summary;
  }

  if (evolving === currentSource) {
    return summary;
  }

  await writeScript(studio, targetPath, evolving);
  return summary;
}
