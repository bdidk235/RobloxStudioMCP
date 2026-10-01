/**
 * Waiting for a condition, entirely host-side.
 *
 * Ported from `python/src/roblox_studio_mcp/extended/waiting.py`. Every
 * non-obvious detail in that file is load-bearing and preserved here; the
 * reasoning lives in the Python docstrings, which are quoted where it matters.
 *
 * Why host-side and not in Luau
 * ------------------------------
 * Three ways to wait were tried and all three fail, for different reasons:
 *
 * 1. **Sleeping in the agent's code runtime.** It has no timer, so
 *    `await new Promise(...)` hangs until timeout. Not a Luau problem at all.
 * 2. **`task.spawn` inside a tool call.** The spawned thread dies with the call's
 *    context, so it never runs to completion. Worse, if it dies mid-test-call
 *    Studio's test service wedges permanently.
 * 3. **A single blocking tool call.** Works, but is capped by the client timeout
 *    (120 s by default, and only the *first* leg of it is ours to choose).
 *
 * This MCP server *is* a real Node process with real timers, so the wait belongs
 * here: a loop of small `execute_luau` calls, none of which needs to persist
 * between calls. That is why this needs no persistent Luau thread, and it is the
 * reason the whole thing works where the other three did not.
 */

import type { RobloxStudio } from "../roblox.js";
import {
  DATAMODAL_UNAVAILABLE,
  INVALID_ARGUMENT,
  LUA_ERROR,
  ToolError,
  classify,
  describe,
} from "./errors.js";

/** First poll delay, seconds. Small, because most conditions resolve fast. */
export const MIN_POLL = 0.25;

/** Ceiling on the poll interval, so a long wait stops hammering Studio. */
export const MAX_POLL = 2.0;

/** Growth per poll once the condition looks settled. */
export const BACKOFF = 1.5;

/**
 * Hard ceiling on the wait. Chosen so the wait budget *plus* one in-flight poll
 * still fits inside the 120 s client timeout: the budget is a target, and the
 * final poll can start just before it expires and run long. 90 + 30 = 120, so a
 * caller always gets a verdict rather than an opaque client-side timeout.
 */
export const MAX_WAIT = 90.0;

/** Identical consecutive results after which the wait is treated as settled. */
export const SETTLED_AFTER = 3;

/**
 * Per-poll ceiling, seconds. A healthy poll returns in ~1 s. A command Studio
 * cannot parse has wedged command execution before - measured 2026-09-30,
 * twice: the await never resolved and, requests being sequential, the wedged
 * call blocked every later one until the MCP was reconnected. Bounding the
 * await keeps the loop alive. Losing the race does NOT cancel Studio-side
 * execution - it only frees this end - so consecutive hangs mean the Studio
 * side is wedged, not slow, and retrying is burning budget on polls that
 * cannot return.
 */
export const POLL_TIMEOUT = 30.0;

/**
 * Consecutive hung polls before giving up. One hang could be a slow Studio;
 * three in a row is a wedged one. The abort names the restart that fixes it,
 * because the alternative - burning the whole budget one dead poll at a time -
 * reports a timeout that reads as "condition never true" for a condition that
 * never ran.
 */
export const MAX_HUNG_POLLS = 3;

/**
 * A Luau long-bracket string literal, safe for any input.
 *
 * A long string is `[` + N `=` + `[` ... `]` + N `=` + `]`. Two things took a
 * wrong implementation to find:
 *
 * - **Only `=` is a level indicator.** `]` is not, so `[]]` is not a valid
 *   opener. Emitting it for a value containing no bracket produced code
 *   `execute_luau` rejected with "Failed to parse command code", so a perfectly
 *   good condition looked like a broken tool.
 * - A level-0 string is closed by the first `]]`, so `]]` anywhere in the value
 *   truncates it. Any other lone `]` is harmless, and `=` is harmless at any
 *   level.
 *
 * So one rule covers everything: **use level 1 exactly when the value contains
 * `]]`**, where the closer is `]=]` and a bare `]]` cannot match it.
 */
export function luauString(value: string): string {
  const pad = value.includes("]]") ? "=" : "";
  return `[${pad}[${value}]${pad}]`;
}

/**
 * All three DataModels are supported, and `loadstring` was the only thing
 * stopping that. It does not resolve in play mode (user-confirmed, from a live
 * failure: `loadstring() is not available` in `AssistantCommand`), so a caller
 * waiting on a condition in play mode got a fault that had nothing to do with
 * their condition. Removing it makes the `pcall` form work in every mode; play
 * mode was collateral damage from the wrong fix, not a limitation of waiting
 * itself.
 *
 * The residual hazard is narrower and is a Studio bug rather than ours - see
 * `PROBE_CAVEAT`. A hung poll no longer wedges this server (each poll's await
 * is bounded), but nothing here can un-wedge Studio itself, so consecutive
 * hangs abort with the restart that fixes them.
 */
export const PROBE_CAVEAT =
  "a condition that does not parse is compiled as part of the command sent to " +
  "Studio, and such a command has wedged Studio's command execution before; the " +
  "parse happens before any handler runs, so a hung poll is bounded client-side " +
  "but only a Studio restart clears the wedge. Prefer simple comparisons.";

/**
 * Wrap a caller condition so one call returns everything we need.
 *
 * **No `loadstring`.** It was introduced to isolate a caller condition that failed
 * to parse, and it did that job in Edit mode. But it does not resolve in play
 * mode, so the tool was advertising a `datamodel_type` it could not honour - a
 * technique that only works in one of two supported contexts is a trap, not a
 * technique. Play mode works without it; the `pcall` form never needed it for
 * anything except parse isolation.
 *
 * The condition is spliced in as **code**, not as a string literal. That
 * distinction is the whole ballgame and it was got wrong once already: an earlier
 * version of this fix kept `luauString()` and emitted
 * `return ([[#game:GetDescendants() > 50]])`, which returns the *string*
 * `"#game:GetDescendants() > 50"`. That string is truthy, so every wait reported
 * `satisfied: true` on the first poll **without evaluating the condition at all** -
 * a silent false success, the exact class this project exists to prevent. Caught
 * only because the verdict's non-boolean warning fired.
 *
 * So: raw splice. A condition that parses is evaluated, and one that *faults* is
 * caught and reported as `THREW` rather than crashing the poll. A condition that
 * does not *parse* is not catchable here at all; see `PROBE_CAVEAT`.
 */
export function buildProbe(condition: string): string {
  return (
    "local ok, value = pcall(function()\n" +
    "  return (" + condition + ")\n" +
    "end)\n" +
    'if not ok then return "false\\tTHREW\\t" .. tostring(value) end\n' +
    "return tostring(value)\n"
  );
}

export interface ProbeResult {
  ok: boolean;
  value: string | null;
  fault: "ERR" | "THREW" | null;
  detail: string;
}

/**
 * Read the probe reply into ok / value / fault.
 *
 * `fault` is `ERR` when the condition would not compile, `THREW` when it
 * compiled but faulted, and null when it simply evaluated. Both faults are
 * raised by the caller, because a condition that never runs must not be reported
 * as a timeout.
 */
export function parseProbe(raw: string): ProbeResult {
  const text = raw.trim();
  const parts = text.split("\t");
  // A bare value means the condition returned something falsy-looking; that is
  // still a successful evaluation.
  if (parts.length === 0 || !parts[0]) {
    return { ok: false, value: null, fault: "THREW", detail: text.slice(0, 200) || "empty reply" };
  }
  const head = parts[0];
  const rest = parts.slice(1).join("\t");
  if (head === "false" && parts.length >= 2 && (parts[1] === "ERR" || parts[1] === "THREW")) {
    return { ok: false, value: null, fault: parts[1] as "ERR" | "THREW", detail: rest.slice(0, 200) };
  }
  return { ok: true, value: head, fault: null, detail: rest.slice(0, 200) };
}

/**
 * **No `warning`.** Mirrors the Python removal: the field existed for one
 * producer - a truthy non-boolean, returned as `satisfied: true` with a caveat
 * beside it. That case now raises `INVALID_ARGUMENT`, so nothing could produce
 * the field and it would have shipped as a permanent `null`. A field that is
 * always null invites a caller to check it, find nothing, and conclude all is
 * well - which is the opposite of what a warning is for.
 */
export interface Verdict {
  satisfied: boolean;
  timed_out: boolean;
  last_value: string | null;
  polls: number;
  timeout_seconds: number;
  final_poll_seconds: number;
  settled_repeats: number;
  poll_errors: string[];
}

function verdict(
  satisfied: boolean,
  last: string | null,
  polls: number,
  budget: number,
  delay: number,
  repeats: number,
  errors: string[],
): Verdict {
  return {
    satisfied,
    timed_out: !satisfied,
    last_value: last,
    polls,
    timeout_seconds: budget,
    final_poll_seconds: Number(delay.toFixed(3)),
    settled_repeats: repeats,
    poll_errors: errors.slice(0, 3),
  };
}

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/** Monotonic-ish clock, injected so tests need not wait in real time. */
export type Clock = () => number;
const defaultClock: Clock = () => Date.now();

export interface WaitForOptions {
  datamodelType?: string;
  /** Per-poll await bound, seconds. Test seam; defaults to POLL_TIMEOUT. */
  pollTimeoutSeconds?: number;
  /** Test seam. Defaults to the real clock and real timers. */
  clock?: Clock;
  sleep?: (seconds: number) => Promise<void>;
}

/**
 * Poll `condition` until it is true, or the deadline passes.
 *
 * Returns a verdict rather than throwing on timeout, because "not yet" is a
 * normal answer here and the caller usually wants to see the last value.
 *
 * The clock and sleep are injectable because the alternative is a test suite
 * that takes 90 seconds to prove a timeout works.
 */
export async function waitFor(
  studio: RobloxStudio,
  condition: string,
  timeoutSeconds: number,
  options: WaitForOptions = {},
): Promise<Verdict> {
  if (!condition || condition.trim() === "") {
    throw new ToolError(INVALID_ARGUMENT, "condition must be a non-empty expression");
  }

  const now = options.clock ?? defaultClock;
  const nap = options.sleep ?? ((seconds: number) => sleep(seconds * 1000));
  const datamodelType = options.datamodelType ?? "Edit";

  const budget = Math.max(MIN_POLL, Math.min(Number(timeoutSeconds), MAX_WAIT));
  const probe = buildProbe(condition);
  const deadline = now() + budget * 1000;
  const pollTimeout = (options.pollTimeoutSeconds ?? POLL_TIMEOUT) * 1000;

  let delay = MIN_POLL;
  let last: string | null = null;
  let repeats = 0;
  let polls = 0;
  let hung = 0;
  const errors: string[] = [];

  const pollOnce = (): Promise<{ text(): string }> => {
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("poll-timeout")), pollTimeout);
    });
    const attempt = (async () => {
      const result = await studio.call("execute_luau", {
        code: probe,
        datamodel_type: datamodelType,
      });
      return result as { text(): string };
    })();
    return Promise.race([attempt, timeout]).finally(() => clearTimeout(timer));
  };

  for (;;) {
    polls += 1;
    let text: string;
    try {
      const result = await pollOnce();
      text = typeof result?.text === "function" ? result.text() : String(result?.text ?? "");
    } catch (exc) {
      if (exc instanceof Error && exc.message === "poll-timeout") {
        // The Studio side never replied. Losing the race frees this end only;
        // Studio-side execution is unaffected, so a second hang is evidence
        // the wedge is over there, not here.
        hung += 1;
        errors.push(`poll ${polls} hung (no reply within ${(pollTimeout / 1000).toFixed(0)}s)`);
        if (hung >= MAX_HUNG_POLLS) {
          throw new ToolError(
            "TIMEOUT",
            `condition never executed: ${MAX_HUNG_POLLS} consecutive polls hung with ` +
              "no reply. Studio-side command execution is likely wedged - an " +
              "unparseable condition does this - and reconnecting this server " +
              "will not clear it. Restart the Studio, fix the condition, and try again.",
            { condition },
          );
        }
        await nap(delay);
        delay = Math.min(delay * BACKOFF, MAX_POLL);
        continue;
      }
      // A probe fault is thrown deliberately below and must not be swallowed by
      // the poll-error handler, or a typo would become a 90-second wait.
      if (exc instanceof ToolError) {
        throw exc;
      }
      const err = classify(exc);
      // A missing DataModel will not appear on its own, so surface it now rather
      // than after burning the whole budget on a doomed wait.
      if (err.code === DATAMODAL_UNAVAILABLE) {
        throw err;
      }
      errors.push(err.message.slice(0, 160));
      await nap(delay);
      delay = Math.min(delay * BACKOFF, MAX_POLL);
      continue;
    }
    try {
      hung = 0; // any reply proves Studio executed something; only hangs count
      const parsed = parseProbe(text);
      if (!parsed.ok) {
        // A condition that never runs must not be reported as a timeout: that is
        // what turns a typo into a 90-second wait and no answer.
        if (parsed.fault === "ERR") {
          throw new ToolError(
            INVALID_ARGUMENT,
            `condition does not compile: ${parsed.detail}`,
            { condition },
          );
        }
        throw new ToolError(
          LUA_ERROR,
          `condition raised at runtime: ${parsed.detail}`,
          { condition },
        );
      }

      if (parsed.value === last) {
        repeats += 1;
      } else {
        repeats = 0;
        last = parsed.value;
      }

      // A truthy non-boolean is the one case that can manufacture a false
      // success, so it is refused rather than reported with a caveat. `nil` is
      // NOT in this branch: it is falsy in Lua and `return workspace.Foo` is a
      // normal condition, not a mistake.
      if (parsed.value !== "true" && parsed.value !== "false" && parsed.value !== "nil") {
        throw new ToolError(
          "INVALID_ARGUMENT",
          `condition returned ${JSON.stringify(parsed.value)}, not a boolean; ` +
            "in Lua 0 and '' are both truthy, so this would have been reported " +
            "as satisfied. Return true or false - `return workspace.Foo` is " +
            "fine, but compare it or wrap it in a `and .. or false`.",
          { condition, returned: parsed.value },
        );
      }
      const satisfied = parsed.value === "true";
      if (satisfied) {
        return verdict(true, last, polls, budget, delay, repeats, errors);
      }

      const remainingMs = deadline - now();
      if (remainingMs <= 0) {
        return verdict(false, last, polls, budget, delay, repeats, errors);
      }
      await nap(Math.min(delay, remainingMs / 1000));
      delay = repeats >= SETTLED_AFTER
        ? Math.min(delay * BACKOFF, MAX_POLL)
        : Math.min(delay * 1.25, MAX_POLL);
    } catch (exc) {
      // A probe fault is thrown deliberately above and must not be swallowed by
      // the poll-error handler, or a typo would become a 90-second wait.
      if (exc instanceof ToolError) {
        throw exc;
      }
      const err = classify(exc);
      // A missing DataModel will not appear on its own, so surface it now rather
      // than after burning the whole budget on a doomed wait.
      if (err.code === DATAMODAL_UNAVAILABLE) {
        throw err;
      }
      errors.push(err.message.slice(0, 160));
      await nap(delay);
      delay = Math.min(delay * BACKOFF, MAX_POLL);
    }
  }
}
