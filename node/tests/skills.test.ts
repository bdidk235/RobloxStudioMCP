import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  SKILL_IGNORED,
  SkillError,
  callSkill,
  findSkillsDir,
  getSkill,
  loadSkills,
  skillIndex,
} from "../src/extended/skills.js";

const SKILL = `---
name: %NAME%
description: %DESC%
---

# %NAME%

body text here
`;

function write(dir: string, name: string, text: string): void {
  writeFileSync(join(dir, name), text, "utf8");
}

describe("real skills", () => {
  it("finds the skills folder", () => {
    expect(findSkillsDir()).not.toBeNull();
  });

  it("loads the shipped skills in a stable order", () => {
    const skills = loadSkills();
    expect(skills.length).toBeGreaterThanOrEqual(5);
    const names = skills.map((s) => s.name);
    expect(names).toEqual([...names].sort());
    for (const skill of skills) {
      expect(skill.name).toMatch(/^rsx-/);
      expect(skill.content.trim().length).toBeGreaterThan(0);
    }
  });

  it("keeps the index far smaller than the bodies", () => {
    // The index is paid on every call; bodies only on demand. If this inverts,
    // the design has stopped paying for itself.
    const skills = loadSkills();
    const indexChars = skillIndex(skills).length;
    const bodyChars = skills.reduce((n, s) => n + s.content.length, 0);
    expect(indexChars).toBeLessThan(bodyChars / 4);
  });

  it("keeps descriptions to one short line", () => {
    for (const skill of loadSkills()) {
      expect(skill.description.length, skill.name).toBeLessThanOrEqual(160);
      expect(skill.description).not.toContain("\n");
    }
  });

  it("drops the frontmatter when a skill is fetched", () => {
    const text = callSkill("rsx-transport");
    expect(text.startsWith("# rsx-transport")).toBe(true);
    expect(text.split("\n\n")[0]).not.toContain("description:");
    expect(text.length).toBeGreaterThan(500);
  });

  it("suggests a near miss on a typo", () => {
    expect(() => getSkill("rsx-transprot")).toThrow(/rsx-transport/);
  });

  it("loads the same files the Python loader does", () => {
    // Deliberately not a cross-process comparison. Each suite parses the real
    // skill files, so a divergence in the frontmatter contract fails on both
    // sides anyway - that is how the \A-is-not-a-JS-anchor bug was caught here.
    // Shelling out to Python from inside the test runner proved to depend on
    // the runner's environment (spawnSync ENOENT under vitest, fine in plain
    // node), which is a fragile dependency for redundant coverage.
    const skills = loadSkills();
    expect(skills.length).toBeGreaterThan(0);
    for (const skill of skills) {
      expect(skill.name).toBe(`${skill.path.split(/[\\/]/).pop()!.replace(/\.md$/, "")}`);
      expect(skill.source).toBe("RobloxStudioMCP");
    }
  });
});

describe("loader edge cases", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "skills-test-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("reports a missing frontmatter block", () => {
    write(dir, "rsx-bad.md", "# no frontmatter here\n");
    expect(() => loadSkills(dir)).toThrow(/frontmatter/);
  });

  it("reports a name that disagrees with the filename", () => {
    write(dir, "rsx-wrong.md", SKILL.replace(/%NAME%/g, "rsx-other").replace(/%DESC%/g, "d"));
    expect(() => loadSkills(dir)).toThrow(/does not match/);
  });

  it("reports a missing description", () => {
    write(dir, "rsx-nodesc.md", "---\nname: rsx-nodesc\n---\n\nbody\n");
    expect(() => loadSkills(dir)).toThrow(/description/);
  });

  it("does not treat README.md as a skill", () => {
    write(dir, "README.md", "# readme\n");
    write(dir, "rsx-ok.md", SKILL.replace(/%NAME%/g, "rsx-ok").replace(/%DESC%/g, "d"));
    expect(loadSkills(dir).map((s) => s.name)).toEqual(["rsx-ok"]);
    expect(SKILL_IGNORED.has("README.md")).toBe(true);
  });

  it("accepts CRLF frontmatter", () => {
    write(dir, "rsx-crlf.md", "---\r\nname: rsx-crlf\r\ndescription: d\r\n---\r\n\r\nbody\r\n");
    const skills = loadSkills(dir);
    expect(skills[0]!.name).toBe("rsx-crlf");
    expect(skills[0]!.content).toContain("body");
  });

  it("returns empty for a missing directory rather than throwing", () => {
    expect(loadSkills(join(dir, "nope"))).toEqual([]);
  });

  it("says so clearly when the folder holds no skills", () => {
    expect(skillIndex(loadSkills(dir))).toContain("No skills found");
  });
});
