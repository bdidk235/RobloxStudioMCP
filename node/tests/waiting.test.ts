/**
 * The wait loop's probe wrapper, ported from `python/tests/test_waiting.py`.
 *
 * This gets the most attention because the wrapper had a real bug, twice.
 * First it was `pcall(function() return (<cond>) end)`, and a condition that
 * failed to parse **wedged Studio's command execution** - the whole
 * `execute_luau` call never returned, so the client sat until the 120 s
 * timeout. Then an unparseable condition wedged Studio-side execution
 * persistently (measured 2026-09-30, twice), and the poll's unbounded await
 * wedged the sequential server loop with it until reconnect. The fix is two
 * parts: the condition is spliced as bare code inside `pcall` (no `loadstring`
 * - it does not resolve in play mode), and each poll's await is bounded,
 * aborting after `MAX_HUNG_POLLS` consecutive hangs with the restart named.
 *
 * Mirrored rather than rewritten on purpose: the two `_luau_string` rules below
 * each cost a real debugging session to find, and a re-derivation is how they get
 * lost.
 */
import { describe, expect, it } from "vitest";

import {
  BACKOFF,
  MAX_HUNG_POLLS,
  MAX_POLL,
  MAX_WAIT,
  MIN_POLL,
  POLL_TIMEOUT,
  SETTLED_AFTER,
  buildProbe,
  luauString,
  parseProbe,
  waitFor,
} from "../src/extended/waiting.js";

describe("probe wrapper", () => {
  it("emits no loadstring", () => {
    // `loadstring` was removed, and this is the test that keeps it removed. It
    // does not resolve in play mode - user-confirmed, from a live
    // `loadstring() is not available` in a script running under play. So a probe
    // using it worked in Edit and faulted in Client/Server: a tool advertising a
    // `datamodel_type` it cannot honour.
    for (const condition of ["1 + 1", "true", "1 +", "#Players:GetPlayers() >= 3"]) {
      expect(buildProbe(condition), condition).not.toContain("loadstring");
    }
  });

  it("wraps the condition in a pcall so a fault is data", () => {
    // A condition that *faults* must come back as THREW, not crash the poll.
    const code = buildProbe("error('x')");
    expect(code).toContain("pcall(function()");
    expect(code).toContain("THREW");
  });

  it("splices the condition as code, not as a string literal", () => {
    // The bug this pins, and it is the worst kind: a silent false success. An
    // earlier version kept `luauString()` and emitted
    // `return ([[#game:GetDescendants() > 50]])`. A long-bracket string is a
    // *literal*, so the probe returned the string - which is truthy - and every
    // wait reported `satisfied: true` on the first poll without evaluating the
    // condition. Found live, because the verdict's non-boolean warning fired.
    const code = buildProbe("#game:GetDescendants() > 50");
    expect(code).toContain("return (#game:GetDescendants() > 50)");
    expect(code).not.toContain("[[");
    expect(code).not.toContain("]]");
  });

  it("a true condition is code, not a literal", () => {
    // The `return (...)` has to be *inside* the function. Compiling the caller's
    // text as a statement rejected `true`, which is not a Luau statement, so the
    // first real condition failed to compile.
    expect(buildProbe("true")).toContain("return (true)");
  });

  it("evaluates rather than echoes", () => {
    // A structural check, since string equality cannot tell an echo from an
    // evaluation. Inside the function body it runs; inside a long-bracket
    // literal it comes back verbatim.
    const body = buildProbe("#Players:GetPlayers() >= 3").split("pcall(function()")[1] ?? "";
    expect(body).toContain("#Players:GetPlayers() >= 3");
    const inner = (body.split("return (")[1] ?? "").split(")")[0] ?? "";
    expect(inner.trim().startsWith("[")).toBe(false);
  });

  it("no longer claims to catch parse errors", () => {
    // It cannot. Without `loadstring` there is no compile step to intercept, so
    // the probe has no ERR branch - and asserting one would be asserting a
    // capability the code does not have. The hazard is documented in
    // `PROBE_CAVEAT` instead of papered over with an unreachable branch.
    const code = buildProbe("1 +");
    expect(code).not.toContain("ERR");
    expect(code).not.toContain("if not chunk");
  });
});

describe("luauString", () => {
  it("uses the plain bracket form for a plain value", () => {
    // The bug: `]` was treated as a level indicator, so a value with none
    // produced `[]]`, which does not parse.
    expect(luauString("true")).toBe("[[true]]");
    expect(luauString("")).toBe("[[]]");
  });

  it("never emits a bracket as the level indicator", () => {
    // Only `=` is a level indicator; `[]]` is not a valid opener.
    for (const text of ["true", "a", "]]", "x]y", "]", "]]]"]) {
      const literal = luauString(text);
      const opener = literal.slice(0, literal.indexOf("[", 1));
      expect(opener, text).not.toContain("]");
    }
  });

  it("escalates to level one only for a double bracket", () => {
    // A lone `]` is harmless at level 0: the string closes on the first `]]`, so
    // `]]` alone is what forces level 1.
    for (const text of ["]", "x]", "a]b", "]x["]) {
      expect(luauString(text), text).toBe(`[[${text}]]`);
    }
    for (const text of ["a]]b", "]]", "]]]", "a]]b]]c"]) {
      const literal = luauString(text);
      expect(literal.startsWith("[=["), `${text} -> ${literal}`).toBe(true);
      expect(literal.endsWith("]=]"), `${text} -> ${literal}`).toBe(true);
    }
  });

  it("treats equals as harmless at any level", () => {
    expect(luauString("a=b")).toBe("[[a=b]]");
    expect(luauString("a]=b")).toBe("[[a]=b]]");
    expect(luauString("a]==b")).toBe("[[a]==b]]");
    expect(luauString("a]==]b")).toBe("[[a]==]b]]");
    // A real `]]` adjacent in the value forces level 1.
    expect(luauString("a]]=b")).toBe("[=[a]]=b]=]");
    expect(luauString("a]=]b")).toBe("[[a]=]b]]");
  });

  it("needs no escaping and emits none", () => {
    // A backslash inside a long-bracket string is a literal backslash, so
    // escaping it would corrupt the value.
    const literal = luauString('say "hi" \\ and \n newline');
    expect(literal).toContain('say "hi" \\ and \n newline');
  });
});

describe("parseProbe", () => {
  it("reads a plain true", () => {
    const got = parseProbe("true");
    expect(got.ok).toBe(true);
    expect(got.value).toBe("true");
    expect(got.fault).toBeNull();
  });

  it("treats false as a successful evaluation", () => {
    const got = parseProbe("false");
    expect(got.ok).toBe(true);
    expect(got.value).toBe("false");
  });

  it("reads a numeric value", () => {
    const got = parseProbe("3");
    expect(got.ok).toBe(true);
    expect(got.value).toBe("3");
  });

  it("reads a syntax fault", () => {
    const got = parseProbe("false\tERR\tunexpected symbol near '1 +'");
    expect(got.ok).toBe(false);
    expect(got.fault).toBe("ERR");
    expect(got.detail).toContain("unexpected symbol");
  });

  it("reads a runtime fault", () => {
    const got = parseProbe("false\tTHREW\tscript is not a valid member");
    expect(got.ok).toBe(false);
    expect(got.fault).toBe("THREW");
  });

  it("treats an empty reply as a fault, not a pass", () => {
    // An empty answer must never read as a satisfied condition.
    const got = parseProbe("");
    expect(got.ok).toBe(false);
    expect(got.fault).not.toBeNull();
  });
});

describe("polling constants", () => {
  it("backs off within bounds", () => {
    expect(MIN_POLL).toBeLessThan(MAX_POLL);
    expect(BACKOFF).toBeGreaterThan(1.0);
    expect(SETTLED_AFTER).toBeGreaterThan(1);
  });

  it("keeps a wait inside the client timeout", () => {
    // 120 s is the client default. Exceeding it means being cut off with no
    // diagnostic instead of a clean timeout verdict. The budget is a target: the
    // last poll can start just before expiry and then run long, so budget plus a
    // worst-case poll must fit - pinned against the constant, not a copy of it.
    const CLIENT_TIMEOUT = 120.0;
    expect(MAX_WAIT).toBeLessThan(CLIENT_TIMEOUT);
    expect(MAX_WAIT + POLL_TIMEOUT).toBeLessThanOrEqual(CLIENT_TIMEOUT);
  });
});

describe("waitFor hung polls", () => {
  // Measured 2026-09-30, twice: an unparseable condition wedged Studio-side
  // command execution, the poll's await never resolved, and the sequential
  // server loop wedged with it until the MCP was reconnected. Mirrors
  // `python/tests/test_waiting.py::TestHungPolls`: the fake hangs the same way
  // (an await that never resolves) and the test proves the wait aborts instead
  // of hanging with it.
  const scriptedStudio = (script: string[], calls: { n: number }) => ({
    call: async (_name: string, _args: unknown) => {
      calls.n += 1;
      const step = script[Math.min(calls.n - 1, script.length - 1)];
      if (step === "hang") await new Promise(() => {});
      return { text: () => step };
    },
  });
  const fast = { sleep: async (_s: number) => {}, pollTimeoutSeconds: 0.05 };

  it("aborts consecutive hangs with the restart", async () => {
    const calls = { n: 0 };
    const studio = scriptedStudio(["hang"], calls);
    let thrown: unknown;
    try {
      await waitFor(studio as never, "1 +", 90, fast);
    } catch (e) {
      thrown = e;
    }
    expect((thrown as { code?: string })?.code).toBe("TIMEOUT");
    expect(String((thrown as { message?: string })?.message)).toContain("never executed");
    expect(String((thrown as { message?: string })?.message)).toContain("Restart the Studio");
    expect(calls.n).toBe(MAX_HUNG_POLLS);
  });

  it("resets the hang count on a reply", async () => {
    // hang, hang, reply, hang, hang, hang: the middle reply proves Studio ran
    // something, so the abort lands three hangs later - six polls, not three.
    const calls = { n: 0 };
    const studio = scriptedStudio(["hang", "hang", "false", "hang", "hang", "hang"], calls);
    let thrown: unknown;
    try {
      await waitFor(studio as never, "1 +", 90, fast);
    } catch (e) {
      thrown = e;
    }
    expect((thrown as { code?: string })?.code).toBe("TIMEOUT");
    expect(calls.n).toBe(6);
  });

  it("absorbs a single hang in a healthy wait", async () => {
    // One hang could be a slow Studio. The wait records it as a poll error and
    // the healthy verdict still lands.
    const calls = { n: 0 };
    const studio = scriptedStudio(["hang", "false", "true"], calls);
    const verdict = await waitFor(studio as never, "1 +", 90, fast);
    expect(verdict.satisfied).toBe(true);
    expect(verdict.polls).toBe(3);
    expect(verdict.poll_errors).toHaveLength(1);
  });
});
