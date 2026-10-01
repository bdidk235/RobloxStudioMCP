/**
 * Skill library for this MCP, loaded from the repository's `skills/` folder.
 *
 * Why this exists
 * ---------------
 * Roblox's relayed tool set already ships a `skill` tool with a good set of
 * engine-level recipes (`rbx-debug`, `rbx-device-simulator-lua`,
 * `rbx-perf-profiling`, `rbx-scene-analysis`, `rbx-unit-test`). Those cover the
 * *engine*. What they do not cover is *this transport*, and the transport is
 * where the traps are: the 100,015-character return truncation, the
 * 6,291,456-byte scratch-module ceiling, the console arriving as one line with
 * literal newlines, `ContinueExecution = false` hanging the calling tool call, a
 * per-call registry folder that makes `list` look empty, and dot-free instance
 * names.
 *
 * The cost argument is the same one the other implementation uses. A long
 * document read on demand is cheaper than the same prose in a tool description,
 * which is paid on **every call of every session**. Our own descriptions are
 * held under a hard budget by tests, so new detail belongs here.
 *
 * Deliberately narrow: an index of what exists, and one skill at a time. Listing
 * everything eagerly would recreate the problem this is meant to solve.
 *
 * Skill files are markdown with a small frontmatter block:
 *
 *     ---
 *     name: rsx-transport
 *     description: One line, used to build the index.
 *     ---
 *
 * A file whose frontmatter is missing or whose `name` disagrees with the
 * filename is reported as an error rather than skipped, so a typo is visible
 * instead of silently reducing the library.
 *
 * The skills live once, at the repository root, and are found by walking up from
 * this file. That is what lets the Python and Node implementations share a
 * single copy instead of drifting.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Folder name, searched for from this file upwards. */
export const SKILLS_DIRNAME = "skills";

/** Suffix. A skill is a markdown file; nothing else in the folder is loaded. */
export const SKILL_SUFFIX = ".md";

/** Files in the skills folder that are not skills. */
export const SKILL_IGNORED = new Set(["README.md"]);

// `^` rather than Python's `\A`, which is not a JavaScript anchor and would
// match a literal "A" here.
const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/;
const FIELD = /^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/;

export class SkillError extends Error {}

export interface Skill {
  name: string;
  description: string;
  source: string;
  path: string;
  body: string;
  content: string;
}

/**
 * Walk up from `start` looking for a `skills/` folder.
 *
 * Walking up rather than using a fixed relative path is what lets one copy of
 * the skills serve both implementations, and keeps working whether this runs
 * from `src`, from `dist`, or from a bundle.
 */
export function findSkillsDir(start?: string): string | null {
  const here = start ?? fileURLToPath(import.meta.url);
  let current = existsSync(here) && statSync(here).isDirectory() ? here : dirname(here);
  for (;;) {
    const candidate = join(current, SKILLS_DIRNAME);
    if (existsSync(candidate) && statSync(candidate).isDirectory()) return candidate;
    const parent = dirname(current);
    if (parent === current) return null;
    current = parent;
  }
}

/** Return `[fields, body]`, throwing if the block is unusable. */
function parseFrontmatter(text: string, path: string): [Record<string, string>, string] {
  const match = FRONTMATTER.exec(text);
  if (!match) {
    throw new SkillError(`${path}: no frontmatter block (expected a leading '---')`);
  }
  const fields: Record<string, string> = {};
  for (const line of (match[1] ?? "").split(/\r?\n/)) {
    if (!line.trim()) continue;
    const field = FIELD.exec(line.trim());
    if (field) fields[field[1]!.trim().toLowerCase()] = field[2]!.trim();
  }
  for (const required of ["name", "description"]) {
    if (!fields[required]) {
      throw new SkillError(`${path}: frontmatter is missing '${required}'`);
    }
  }
  return [fields, text.slice(match[0].length)];
}

/**
 * Read every skill, sorted by name.
 *
 * Sorting is not cosmetic: the index is what a caller sees, and a stable order
 * means the same skill always appears in the same place.
 */
export function loadSkills(directory?: string): Skill[] {
  const root = directory ?? findSkillsDir();
  if (!root || !existsSync(root)) return [];
  const out: Skill[] = [];
  for (const entry of readdirSync(root).sort()) {
    if (!entry.endsWith(SKILL_SUFFIX) || SKILL_IGNORED.has(entry)) continue;
    const path = join(root, entry);
    const text = readFileSync(path, "utf8");
    const [fields, body] = parseFrontmatter(text, entry);
    const expected = entry.slice(0, -SKILL_SUFFIX.length);
    if (fields["name"] !== expected) {
      throw new SkillError(
        `${entry}: frontmatter name ${JSON.stringify(fields["name"])} does not match ` +
          `the filename ${JSON.stringify(expected)}`,
      );
    }
    out.push({
      name: fields["name"]!,
      description: fields["description"]!,
      source: "RobloxStudioMCP",
      path,
      body: text,
      content: body.trim(),
    });
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** Render the catalogue. Deliberately without bodies, to stay cheap. */
export function skillIndex(skills?: Skill[]): string {
  const entries = skills ?? loadSkills();
  if (entries.length === 0) {
    return (
      "No skills found. Expected a 'skills/' folder beside the package, " +
      "with one markdown file per skill."
    );
  }
  const lines = ["<available_skills>"];
  for (const skill of entries) {
    lines.push("  <skill>");
    lines.push(`    <name>${skill.name}</name>`);
    lines.push(`    <source>${skill.source}</source>`);
    lines.push(`    <description>${skill.description}</description>`);
    lines.push("  </skill>");
  }
  lines.push("</available_skills>");
  lines.push("");
  lines.push(
    "Transport traps only. For engine topics use the relayed 'skill' tool: " +
      "rbx-debug, rbx-device-simulator-lua, rbx-perf-profiling, " +
      "rbx-scene-analysis, rbx-unit-test, rbx-docs-search.",
  );
  return lines.join("\n");
}

/**
 * Return one skill, or throw naming the closest matches.
 *
 * A near-miss gets a suggestion rather than a bare "not found", because the
 * names are short and easy to mistype.
 */
export function getSkill(name: string, skills?: Skill[]): Skill {
  const entries = skills ?? loadSkills();
  const hit = entries.find((s) => s.name === name);
  if (hit) return hit;
  const known = entries.map((s) => s.name);
  const lower = name.toLowerCase();
  const close = known.filter((n) => lower.includes(n.toLowerCase()) || n.toLowerCase().includes(lower));
  const hint = close.length > 0 ? ` Did you mean: ${close.join(", ")}?` : "";
  throw new SkillError(
    `no skill named ${JSON.stringify(name)}.${hint} Available: ${known.join(", ") || "(none)"}`,
  );
}

/** Handle a tool call. Returns the text to send back. */
export function callSkill(name?: string | null, directory?: string): string {
  const skills = loadSkills(directory);
  if (!name) return skillIndex(skills);
  const skill = getSkill(name, skills);
  return `# ${skill.name}\n\n_${skill.description}_\n\n---\n\n${skill.content}\n`;
}
