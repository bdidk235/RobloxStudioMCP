/**
 * Error codes, ported from `python/src/roblox_studio_mcp/extended/errors.py`.
 *
 * Why this exists on the Node side: both implementations are supposed to be
 * interchangeable, and until now only Python gave a failure a **stable code**.
 * Node threw bare `Error`, so a caller could not distinguish "you sent a bad
 * argument" from "the engine refused" from "a transport died" - which is the
 * distinction the codes exist to make. A caller porting from one implementation
 * to the other got strictly worse behaviour.
 *
 * The ordering in `classify()` is load-bearing and is preserved exactly:
 * Luau faults are checked **before** the message table, because
 * "Failed to parse command code" contains "parse" and a looser pattern would
 * otherwise claim it. A syntax error in the caller's own Luau is a different
 * class of problem from the engine refusing a request, and the caller has to be
 * able to tell those apart.
 */

// --- codes ------------------------------------------------------------------

export const NO_STUDIO = "NO_STUDIO";
export const STALE_STUDIO_ID = "STALE_STUDIO_ID";
export const AMBIGUOUS_STUDIO = "AMBIGUOUS_STUDIO";
export const DATAMODAL_UNAVAILABLE = "DATAMODAL_UNAVAILABLE";
export const PLACE_NOT_OPEN = "PLACE_NOT_OPEN";
export const NOT_FOUND = "NOT_FOUND";
export const SIZE_LIMIT = "SIZE_LIMIT";
export const TEST_BUSY = "TEST_BUSY";
export const TEST_REFUSED = "TEST_REFUSED";
export const LAUNCH_FAILED = "LAUNCH_FAILED";
export const TIMEOUT = "TIMEOUT";
export const INVALID_ARGUMENT = "INVALID_ARGUMENT";
export const LUA_ERROR = "LUA_ERROR";
export const CAPABILITY_DENIED = "CAPABILITY_DENIED";
export const UNKNOWN = "UNKNOWN";

/** Every code, for tests and for validating anything a caller sends back. */
export const ALL_CODES: ReadonlySet<string> = new Set([
  NO_STUDIO, STALE_STUDIO_ID, AMBIGUOUS_STUDIO, DATAMODAL_UNAVAILABLE,
  PLACE_NOT_OPEN, NOT_FOUND, SIZE_LIMIT, TEST_BUSY, TEST_REFUSED,
  LAUNCH_FAILED, TIMEOUT, INVALID_ARGUMENT, LUA_ERROR, CAPABILITY_DENIED,
  UNKNOWN,
]);

/**
 * Ordered: the first match wins, so the specific patterns precede the general
 * ones. "not connected" would otherwise swallow the ambiguity error.
 *
 * Ported verbatim from the Python tuple, including that ordering.
 */
const PATTERNS: ReadonlyArray<readonly [string, readonly string[]]> = [
  [NO_STUDIO, ["no roblox studio instances are connected"]],
  [STALE_STUDIO_ID, [
    "requested studio_id is not connected",
    "place is not open",
    "not connected",
  ]],
  [AMBIGUOUS_STUDIO, ["more than one studio", "multiple studio", "ambiguous"]],
  [TEST_BUSY, ["a previous one is still in progress"]],
  [TEST_REFUSED, ["addplayers", "endtest", "leavetest"]],
  [DATAMODAL_UNAVAILABLE, ["datamodel is not available", "is not a valid service name"]],
  [CAPABILITY_DENIED, [
    "requires the robloxscript",
    "requires robloxscript",
    "not permitted",
    "plugin security",
    "requires elevated",
  ]],
  [SIZE_LIMIT, ["bad allocation", "out of memory", "string too long"]],
  // LAUNCH_FAILED, at the same position as Python's table. It was MISSING here
  // while the constant was declared, so the code was published and unreachable:
  // every launch failure classified as UNKNOWN on Node and LAUNCH_FAILED on
  // Python, for the same input. Found by an audit that diffed the two tables
  // mechanically - the comment above this array claiming a verbatim port was
  // false, and `parity/tools.json` cannot catch it because that covers schemas
  // and descriptions, not this table.
  [LAUNCH_FAILED, [
    "no complete robloxstudio",
    "is not installed",
    "did not open a place",
    "never opened",
    "mcp is not enabled",
    "could not launch",
    "failed to launch",
    "no robloxstudio executable",
  ]],
  [TIMEOUT, ["timed out", "timeout"]],
  [LUA_ERROR, [
    "attempt to call",
    "attempt to index",
    "attempt to concatenate",
    "attempt to perform arithmetic",
    "is not a valid member",
    "failed to parse",
    "unexpected symbol",
    "invalid value (",
    "unbalanced",
    "malformed",
    "cannot cast",
    "unable to cast",
  ]],
  [NOT_FOUND, [
    "could not find any instances",
    "script not found",
    "not found",
  ]],
];

/**
 * Luau faults: the caller's own code rather than an API refusal. Checked before
 * the message table so a syntax error is never claimed by a looser pattern.
 */
const LUA_FAULT =
  /(attempt to (call|index|concatenate|perform arithmetic)|is not a valid member|failed to parse|unexpected symbol|invalid value \(|unbalanced|malformed|unable to cast|cannot cast)/i;

/** A failure with a stable code and structured data. */
export class ToolError extends Error {
  readonly code: string;
  readonly data: Record<string, unknown>;

  constructor(code: string, message: string, data: Record<string, unknown> = {}) {
    super(message);
    if (!ALL_CODES.has(code)) {
      throw new Error(`unknown error code ${JSON.stringify(code)}`);
    }
    this.name = "ToolError";
    this.code = code;
    this.data = data;
    // Required so `instanceof ToolError` survives the TypeScript
    // downlevel-to-ES5 subclassing that some consumers of this build apply.
    Object.setPrototypeOf(this, ToolError.prototype);
  }

  /** The JSON-RPC `error` object. */
  toError(): Record<string, unknown> {
    const payload: Record<string, unknown> = { code: this.code, message: this.message };
    if (Object.keys(this.data).length > 0) {
      payload["data"] = this.data;
    }
    return payload;
  }
}

/**
 * Render a received argument value for an error message.
 *
 * A message that says "must be a string" without the value cannot tell the
 * caller whether it sent an empty string, 42 or null, and those three need
 * three different fixes.
 *
 * `JSON.stringify(undefined)` is `undefined` rather than a string, so
 * interpolating it directly prints a bare word and loses the distinction from
 * the string "undefined". The explicit branch is what keeps that distinction.
 *
 * This is the mirror of `describe` in
 * `python/src/roblox_studio_mcp/extended/errors.py`, and it produces
 * byte-identical text for the same value - which Python's `repr` would not,
 * since it writes `None` where this writes `null`. `parity/errors.json` pins
 * the result.
 */
export function describe(value: unknown): string {
  if (value === undefined) return "undefined";
  const json = JSON.stringify(value);
  return json === undefined ? String(value) : json;
}

/** True when a message is a Luau runtime fault rather than an API refusal. */
export function isLuaFault(text: string): boolean {
  return LUA_FAULT.test(text);
}

/** Turn any thrown value into a `ToolError`, preserving one that already is. */
export function classify(exc: unknown): ToolError {
  if (exc instanceof ToolError) {
    return exc;
  }

  const text = exc instanceof Error ? exc.message : String(exc);

  if (isLuaFault(text)) {
    return new ToolError(LUA_ERROR, text, { classified_from: "luau-fault-pattern" });
  }

  const lowered = text.toLowerCase();
  for (const [code, needles] of PATTERNS) {
    if (needles.some((n) => lowered.includes(n))) {
      return withRecovery(new ToolError(code, text, { classified_from: "message" }));
    }
  }

  return new ToolError(UNKNOWN, text, { classified_from: "default" });
}

/**
 * What to do next, keyed by code.
 *
 * **Added, not paraphrased.** The rule recorded above `PATTERNS` - keep the
 * engine's message verbatim - exists so a caller's own reading of the text is
 * never second-guessed. That is preserved: the engine's words come through
 * untouched, and the recovery is appended after a blank line so a substring
 * match on the original still works.
 *
 * Only codes where the *fix is deterministic* get an entry. Several codes are
 * genuine dead ends with nothing useful to add, and a recovery line that says
 * "check that it is enabled" teaches a caller to keep trying things.
 *
 * Seeded with one code, from a specific complaint. Widening this is a decision
 * per code, not a formatting pass. Mirrors `_RECOVERY` in errors.py.
 */
const RECOVERY: Readonly<Record<string, string>> = {
  [DATAMODAL_UNAVAILABLE]:
    "A datamodel operation ran where that datamodel does not exist: " +
    "'Edit' needs Edit mode with no play session running, 'Server' and " +
    "'Client' need a running play session. Check the Studio's actual mode " +
    "first (get_studio_state, or the rsx-playtest skill) rather than " +
    "assuming either direction. Note: " +
    "extended_manage_instance(action='stop') terminates the Studio " +
    "process; it does not end a play session.",
};

function withRecovery(error: ToolError): ToolError {
  const recovery = RECOVERY[error.code];
  if (recovery === undefined) return error;
  return new ToolError(error.code, `${error.message}\n\n${recovery}`, {
    ...error.data,
    recovery,
  });
}
