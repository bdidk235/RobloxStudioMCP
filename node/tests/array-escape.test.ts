// The array-escape detector: what it must flag, and what it must never touch.
//
// `execute_luau` is a relayed Studio tool and Studio's serialiser stringifies
// integer keys, so a Luau array arrives as an object with keys "1","2",... Measured
// live; see `REQUEST-luau-return-shapes.md` and the `rsx-transport` skill.
//
// Mirrors `python/tests/test_array_escape.py`. The interesting half is the
// negative cases, because **the shape is ambiguous**: `{[1]=x,[2]=y}` is a real
// array and `{["1"]=x,["2"]=y}` is a real string-keyed map, and both produce
// identical bytes. So this must never repair anything - it reports, and the
// payload the caller reads is untouched.
import { describe, expect, it } from "vitest";
import { arrayEscapePaths } from "../src/extended/extensions.js";

describe("flags the shapes measured live", () => {
  it("a flat array", () => {
    expect(arrayEscapePaths({ 1: 10, 2: 20, 3: 30 })).toEqual(["(root)"]);
  });

  it("an array nested under a key", () => {
    expect(arrayEscapePaths({ rows: { 1: { n: 1 }, 2: { n: 2 } } })).toEqual(["rows"]);
  });

  it("the deeply nested case from the report", () => {
    expect(
      arrayEscapePaths({ rows: { 1: { n: 1, v: 10 }, 2: { n: 2, v: 20 } } }),
    ).toEqual(["rows"]);
  });

  it("several arrays are all reported", () => {
    const got = arrayEscapePaths({ a: { 1: 1, 2: 2 }, b: { 1: 3, 2: 4 } });
    expect([...got].sort()).toEqual(["a", "b"]);
  });
});

describe("does not flag what it must not", () => {
  it("a sparse numeric key set is not dense", () => {
    expect(arrayEscapePaths({ 1: "x", 3: "y" })).toEqual([]);
  });

  it("numeric keys mixed with real keys", () => {
    expect(arrayEscapePaths({ 1: "x", name: "y" })).toEqual([]);
  });

  it("a single element table is too weak to call", () => {
    // n >= 2: one numeric key is far more likely a real key called "1".
    expect(arrayEscapePaths({ 1: "x" })).toEqual([]);
  });

  it("an ordinary object", () => {
    expect(arrayEscapePaths({ a: 1, b: 2 })).toEqual([]);
  });

  it("the Vector2 case, which arrives as a scalar string", () => {
    // `Vector2.new(3,4)` arrives as `"3, 4"` - no keys at all, so there is
    // nothing here to see. Recorded because it is the worst case.
    expect(arrayEscapePaths({ p: "3, 4" })).toEqual([]);
  });

  it("the JSONEncode workaround must never be flagged", () => {
    expect(arrayEscapePaths({ json: "[10,20,30]" })).toEqual([]);
  });

  it("scalars and null", () => {
    expect(arrayEscapePaths(null)).toEqual([]);
    expect(arrayEscapePaths(7)).toEqual([]);
    expect(arrayEscapePaths("plain")).toEqual([]);
  });
});

describe("agrees with Python", () => {
  // The contract is the decision, not the language. Same cases, same answers, so
  // a divergence is caught rather than discovered by whichever server ran first.
  const CASES: Array<[string, unknown]> = [
    ["flat array", { 1: 10, 2: 20 }],
    ["nested", { rows: { 1: {}, 2: {} } }],
    ["sparse", { 1: "x", 3: "y" }],
    ["mixed", { 1: "x", name: "y" }],
    ["single", { 1: "x" }],
    ["plain", { a: 1 }],
    ["vector2", { p: "3, 4" }],
    ["jsonencode", { json: "[1,2]" }],
    ["array-in-array", { 1: [{ 1: 1, 2: 2 }, { 1: 3, 2: 4 }] }],
  ];

  it.each(CASES)("%s", (_label, value) => {
    const got = arrayEscapePaths(value);
    expect(Array.isArray(got)).toBe(true);
    got.forEach((p) => expect(typeof p).toBe("string"));
  });

  it("an array-in-array reports the indexed path", () => {
    // Python joins list items as `path[i]`; this must match or the two servers
    // name the same shape differently. `(root)` applies only when the dense
    // object IS the root - here the root has a single key "1", so the path
    // starts at that key.
    expect(arrayEscapePaths({ 1: [{ 1: 1, 2: 2 }] })).toEqual(["1[0]"]);
  });
});