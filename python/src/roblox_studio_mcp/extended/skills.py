"""Skill library for this MCP, loaded from the repository's ``skills/`` folder.

Why this exists
---------------
Roblox's relayed tool set already ships a ``skill`` tool with a good set of
engine-level recipes (``rbx-debug``, ``rbx-device-simulator-lua``,
``rbx-perf-profiling``, ``rbx-scene-analysis``, ``rbx-unit-test``). Those cover
the *engine*. What they do not cover is *this transport*, and the transport is
where the traps are: the 100,015-character return truncation, the
6,291,456-byte scratch-module ceiling, the console arriving as one line with
literal newlines, ``ContinueExecution = false`` hanging the calling tool call,
a per-call registry folder that makes ``list`` look empty, and dot-free instance
names.

The cost argument is the same one the other implementation uses. A long document
read on demand is cheaper than the same prose in a tool description, which is
paid on **every call of every session**. Our own descriptions are held under a
hard budget by tests, so new detail belongs here.

Deliberately narrow: an index of what exists, and one skill at a time. Listing
everything eagerly would recreate the problem this is meant to solve.

Skill files are markdown with a small frontmatter block::

    ---
    name: rsx-transport
    description: One line, used to build the index.
    ---

A file whose frontmatter is missing or whose ``name`` disagrees with the
filename is reported as an error rather than skipped, so a typo is visible
instead of silently reducing the library.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple, TypedDict, Union

#: Folder name, searched for from this file upwards.
SKILLS_DIRNAME = "skills"

#: Suffix. A skill is a markdown file; nothing else in the folder is loaded.
SKILL_SUFFIX = ".md"

#: Files in the skills folder that are not skills.
SKILL_IGNORED = {"README.md"}

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


class SkillError(RuntimeError):
    """A skill file exists but cannot be used as written."""


class Skill(TypedDict):
    """One loaded skill, as :func:`load_skills` returns it.

    ``content`` and ``body`` are both the text, and keeping both is deliberate:
    ``content`` is the body with the frontmatter already stripped, which is what
    a caller sends to a model, while ``body`` is the file verbatim for anyone
    who needs to re-parse it. Conflating them is how a frontmatter block ends up
    in the middle of a prompt.
    """

    name: str
    description: str
    source: str
    path: str
    body: str
    content: str


class SkillIndexResult(TypedDict):
    """The structured half of :func:`call_skill` when no name was given.

    Index and single-skill are different arms of a union rather than one dict
    with optional keys: the two carry disjoint keys, so a merged shape would let
    ``result["name"]`` type-check on an index response that has no such key.
    """

    skills: List[Dict[str, str]]


class SkillDetailResult(TypedDict):
    """The structured half of :func:`call_skill` when one name was given."""

    name: str
    description: str
    chars: int


SkillCallResult = Union[SkillIndexResult, SkillDetailResult]


def find_skills_dir(start: Optional[str] = None) -> Optional[str]:
    """Walk up from ``start`` looking for a ``skills/`` folder.

    Walking up rather than using a fixed relative path is what lets one copy of
    the skills serve both the Python and Node implementations, and keeps working
    whether the package is imported from the source tree or from site-packages
    inside the repo.
    """
    here = os.path.abspath(start or __file__)
    current = here if os.path.isdir(here) else os.path.dirname(here)
    while True:
        candidate = os.path.join(current, SKILLS_DIRNAME)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _parse_frontmatter(text: str, path: str) -> Tuple[Dict[str, str], str]:
    """Return ``(fields, body)``, raising if the block is unusable.

    The body comes back with the frontmatter already removed, so callers never
    have to strip it a second time and get the boundaries wrong.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        raise SkillError(f"{path}: no frontmatter block (expected a leading '---')")
    fields: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        field = _FIELD.match(line.strip())
        if field:
            fields[field.group(1).strip().lower()] = field.group(2).strip()
    for required in ("name", "description"):
        if not fields.get(required):
            raise SkillError(f"{path}: frontmatter is missing '{required}'")
    return fields, text[match.end() :]


def load_skills(directory: Optional[str] = None) -> List[Skill]:
    """Read every skill, sorted by name.

    Sorting is not cosmetic: the index is what a caller sees, and a stable order
    means the same skill always appears in the same place.
    """
    root = directory or find_skills_dir()
    if not root or not os.path.isdir(root):
        return []
    out: List[Skill] = []
    for entry in sorted(os.listdir(root)):
        if not entry.endswith(SKILL_SUFFIX) or entry in SKILL_IGNORED:
            continue
        path = os.path.join(root, entry)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        fields, body = _parse_frontmatter(text, entry)
        expected = entry[: -len(SKILL_SUFFIX)]
        if fields["name"] != expected:
            raise SkillError(
                f"{entry}: frontmatter name {fields['name']!r} does not match "
                f"the filename {expected!r}"
            )
        out.append(
            {
                "name": fields["name"],
                "description": fields["description"],
                "source": "RobloxStudioMCP",
                "path": path,
                "body": text,
                "content": body.strip(),
            }
        )
    out.sort(key=lambda s: s["name"])
    return out


def skill_index(skills: Optional[List[Skill]] = None) -> str:
    """Render the catalogue. Deliberately without bodies, to stay cheap."""
    entries = skills if skills is not None else load_skills()
    if not entries:
        return (
            "No skills found. Expected a 'skills/' folder beside the package, "
            "with one markdown file per skill."
        )
    lines = ["<available_skills>"]
    for skill in entries:
        lines.append("  <skill>")
        lines.append(f"    <name>{skill['name']}</name>")
        lines.append(f"    <source>{skill['source']}</source>")
        lines.append(f"    <description>{skill['description']}</description>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    lines.append("")
    lines.append(
        "Transport traps only. For engine topics use the relayed 'skill' tool: "
        "rbx-debug, rbx-device-simulator-lua, rbx-perf-profiling, "
        "rbx-scene-analysis, rbx-unit-test, rbx-docs-search."
    )
    return "\n".join(lines)


def get_skill(name: str, skills: Optional[List[Skill]] = None) -> Skill:
    """Return one skill, or raise naming the closest matches.

    A near-miss gets a suggestion rather than a bare "not found", because the
    names are short and easy to mistype.
    """
    entries = skills if skills is not None else load_skills()
    for skill in entries:
        if skill["name"] == name:
            return skill
    known = [s["name"] for s in entries]
    close = [n for n in known if name.lower() in n.lower() or n.lower() in name.lower()]
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise SkillError(f"no skill named {name!r}.{hint} Available: {', '.join(known) or '(none)'}")


def call_skill(
    name: Optional[str] = None,
    directory: Optional[str] = None,
) -> Tuple[str, SkillCallResult]:
    """Handle a tool call. Returns ``(text, structured)``."""
    skills = load_skills(directory)
    if not name:
        return skill_index(skills), {
            "skills": [
                {"name": s["name"], "description": s["description"]} for s in skills
            ]
        }
    skill = get_skill(name, skills)
    header = f"# {skill['name']}\n\n_{skill['description']}_\n\n---\n\n"
    return header + skill["content"] + "\n", {
        "name": skill["name"],
        "description": skill["description"],
        "chars": len(skill["content"]),
    }


__all__ = [
    "SKILLS_DIRNAME",
    "SKILL_SUFFIX",
    "SkillError",
    "Skill",
    "SkillIndexResult",
    "SkillDetailResult",
    "SkillCallResult",
    "find_skills_dir",
    "load_skills",
    "skill_index",
    "get_skill",
    "call_skill",
]
