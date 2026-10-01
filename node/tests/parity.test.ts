/**
 * Node must match `parity/tools.json`, the same contract
 * `python/tests/test_parity.py` asserts.
 *
 * This is the mechanism behind "strong parity". The two implementations drifted
 * for a long time with nothing to notice, because each side's tests only looked
 * at its own tools: Node was missing `extended_wait_for`,
 * `extended_manage_instance` and `extended_clear_breakpoints`, and six schemas
 * differed. The worst kind of drift is a parameter present on one side and
 * absent on the other, because unknown parameters are **silently ignored**
 * (request P0.2a) - the call appears to succeed and returns something other than
 * what was asked for.
 *
 * Run `python parity/build_contract.py` after a deliberate surface change. The
 * diff in the commit message is the parity report.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { EXTENDED_TOOLS, EXTENDED_HANDLERS } from "../src/extendedServer.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");

interface ContractProperty {
  type?: string;
  items_type?: string;
  has_default?: boolean;
  enum?: string[];
}
interface ContractEntry {
  name: string;
  description_chars: number;
  /**
   * Whether the tool only observes. Declared because the test asserts on it -
   * an interface that omits a field the test reads is a hole that lets the
   * assertion compile against nothing, which is the failure class this project
   * exists to prevent. Found by a sibling agent running `tsconfig.check.json`,
   * which type-checks test files where the default `tsc --noEmit` does not.
   */
  read_only: boolean;
  schema: { properties: Record<string, ContractProperty>; required: string[] };
}
interface Contract {
  per_tool_description_cap: number;
  total_description_cap: number;
  total_description_chars: number;
  tool_count: number;
  tools: ContractEntry[];
}

const CONTRACT: Contract = JSON.parse(
  readFileSync(resolve(REPO_ROOT, "parity", "tools.json"), "utf8"),
);

const BY_NAME = new Map(EXTENDED_TOOLS.map((t) => [t.name, t]));

describe("contract is present and coherent", () => {
  it("lists at least one tool", () => {
    expect(CONTRACT.tools.length).toBeGreaterThan(0);
  });

  it("names every tool exactly once", () => {
    const names = CONTRACT.tools.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
  });
});

describe("tool set parity", () => {
  it("implements every tool in the contract", () => {
    const missing = CONTRACT.tools
      .map((t) => t.name)
      .filter((name) => !BY_NAME.has(name));
    expect(missing, `missing from Node: ${missing.join(", ")}`).toEqual([]);
  });

  it("declares no tool the contract does not have", () => {
    const known = new Set(CONTRACT.tools.map((t) => t.name));
    const extra = EXTENDED_TOOLS.map((t) => t.name).filter((name) => !known.has(name));
    expect(extra, `only in Node: ${extra.join(", ")}`).toEqual([]);
  });

  it("has a handler for every tool it declares", () => {
    // A tool with no handler is advertised and then does nothing, which from the
    // caller's side is indistinguishable from a transport fault.
    const orphans = EXTENDED_TOOLS.map((t) => t.name).filter(
      (name) => typeof EXTENDED_HANDLERS[name] !== "function",
    );
    expect(orphans, `declared but not handled: ${orphans.join(", ")}`).toEqual([]);
  });
});

describe("schema parity", () => {
  for (const entry of CONTRACT.tools) {
    describe(entry.name, () => {
      const tool = BY_NAME.get(entry.name);

      it("exists", () => {
        expect(tool, `${entry.name} is missing from Node`).toBeDefined();
      });

      it("agrees on the read-only hint", () => {
        if (!tool) return;
        // The hint tells a client a tool only observes, so it may run those in
        // parallel. Unmarked reads as "may mutate", so omitting it is *safe* but
        // needlessly serial - and a client that grants parallel execution from
        // the hint is why it has to match. Python marks 7 tools; Node marked none
        // until this assertion existed to say so.
        expect(
          Boolean(tool.readOnly),
          `${entry.name}: python read_only=${entry.read_only}, node readOnly=${Boolean(tool.readOnly)}`,
        ).toBe(Boolean(entry.read_only));
      });

      it("declares the same properties", () => {
        if (!tool) return;
        const got = Object.keys(tool.properties).sort();
        const want = Object.keys(entry.schema.properties).sort();
        expect(got, `${entry.name} properties differ`).toEqual(want);
      });

      it("declares the same required arguments", () => {
        if (!tool) return;
        const got = [...tool.required].sort();
        const want = [...entry.schema.required].sort();
        expect(got, `${entry.name} required differs`).toEqual(want);
      });

      it("declares the same property types", () => {
        if (!tool) return;
        for (const [name, spec] of Object.entries(entry.schema.properties)) {
          const mine = (tool.properties as Record<string, { type?: string }>)[name];
          expect(mine, `${entry.name}.${name} missing`).toBeDefined();
          expect(mine?.type, `${entry.name}.${name} type differs`).toBe(spec.type);
        }
      });

      it("agrees on which properties carry a default", () => {
        if (!tool) return;
        for (const [name, spec] of Object.entries(entry.schema.properties)) {
          const mine = (tool.properties as Record<string, { default?: unknown }>)[name];
          const hasDefault = mine !== undefined && "default" in mine;
          expect(
            hasDefault,
            `${entry.name}.${name} default presence differs from the contract`,
          ).toBe(Boolean(spec.has_default));
        }
      });
    });
  }
});

describe("description budget", () => {
  it("keeps every description within the per-tool cap", () => {
    const over = EXTENDED_TOOLS.filter(
      (t) => t.description.length > CONTRACT.per_tool_description_cap,
    ).map((t) => `${t.name} (${t.description.length})`);
    expect(over, `over the ${CONTRACT.per_tool_description_cap} cap: ${over.join(", ")}`).toEqual([]);
  });

  it("keeps the total within the cap", () => {
    // The binding constraint. The tool list is paid on every call of every
    // session, so this is the test that makes a new tool a deliberate decision.
    const total = EXTENDED_TOOLS.reduce((sum, t) => sum + t.description.length, 0);
    expect(
      total,
      `descriptions total ${total}, cap ${CONTRACT.total_description_cap}`,
    ).toBeLessThanOrEqual(CONTRACT.total_description_cap);
  });
});
