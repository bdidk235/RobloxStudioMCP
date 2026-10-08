"""Skill library for this MCP, loaded from the ``skills/`` folder shipped
inside this package.

Why this exists
---------------
Roblox's relayed tool set already ships a ``skill`` tool with a good set of
engine-level recipes (``rbx-debug``, ``rbx-device-simulator-lua``,
``rbx-perf-profiling``, ``rbx-scene-analysis``, ``rbx-unit-test``). Those cover
the *engine*. What they do not cover is *this transport*, and the transport is
where the traps are: the 100,015-character return truncation, the
6,291,456-byte scratch-module ceiling, the console arriving as one line with
literal newlines on some consumers (real newlines on others - match, do not
parse), ``ContinueExecution = false`` hanging the calling tool call,
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
instead of silently reducing the library. The same rule applies one level up:
a missing ``skills/`` folder **raises** instead of yielding an empty list,
because an empty catalogue reads exactly like a working one with nothing in it.
That was the shipped behaviour, measured: the wheel built from this repository
carried all 24 modules and none of the 8 skill files, ``find_skills_dir()``
returned ``None``, ``load_skills()`` returned ``[]`` and the tool served
``skill names: []`` as a successful answer.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple, TypedDict, Union

#: Folder name, relative to the package root.
SKILLS_DIRNAME = "skills"

#: Suffix. A skill is a markdown file; nothing else in the folder is loaded.
SKILL_SUFFIX = ".md"

#: Files in the skills folder that are not skills.
SKILL_IGNORED = {"README.md"}

#: This file: ``.../roblox_studio_mcp/extended/skills.py``.
_HERE = os.path.dirname(os.path.abspath(__file__))

#: The package root: ``.../roblox_studio_mcp``. One level above this file, so
#: it is the same whether the package was imported from a source tree, an
#: editable install or a wheel.
_PACKAGE_ROOT = os.path.dirname(_HERE)

#: Where the skills live in an install. Declared in ``pyproject.toml`` under
#: ``[tool.setuptools.package-data]``, which is what puts the folder in the
#: wheel. Resolved at import time from ``__file__`` rather than from the
#: process CWD, because a server's working directory is its launcher's, not the
#: package's.
_PACKAGED_SKILLS = os.path.join(_PACKAGE_ROOT, SKILLS_DIRNAME)

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


class SkillError(RuntimeError):
    """The skills cannot be used as they are.

    Two shapes, deliberately one exception: a skill file is malformed, or the
    folder holding them is missing or empty. The second is a packaging fault
    rather than a caller fault, and it must not be answerable by a caller that
    reads an empty catalogue as "there are no skills".
    """


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


def _walk_up(start: str) -> Optional[str]:
    """Return the first ``skills/`` folder at or above ``start``."""
    here = os.path.abspath(start)
    current = here if os.path.isdir(here) else os.path.dirname(here)
    while True:
        candidate = os.path.join(current, SKILLS_DIRNAME)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def find_skills_dir(start: Optional[str] = None) -> Optional[str]:
    """Return the folder holding the skills, or ``None`` if there is none.

    With no ``start``:

    1. **The package's own folder** - ``.../roblox_studio_mcp/skills``, the one
       ``[tool.setuptools.package-data]`` puts in the wheel. In an install it is
       the only place the skills can be, and it is checked before any walk so
       that a ``skills/`` directory elsewhere in the tree cannot answer in its
       place.
    2. **A walk up from the package root** - a source-tree fallback, for a
       checkout that has never been installed at all. It is the *only* thing the
       walk is for.

    An earlier version of this function walked up and did nothing else, with a
    docstring claiming that walking up "is what lets one copy of the skills ship
    with the package, and keeps working whether it is imported from the source
    tree or from site-packages". Both halves were false and measured to be
    false: the wheel contained no ``skills/`` directory at all, so the walk
    found nothing in an install and ``load_skills()`` returned ``[]``. The copy
    ships because of the ``package-data`` glob in ``pyproject.toml``; the walk
    only ever finds a source tree.
    """
    if start is not None:
        return _walk_up(start)
    if os.path.isdir(_PACKAGED_SKILLS):
        return _PACKAGED_SKILLS
    return _walk_up(_PACKAGE_ROOT)


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

    **Raises rather than returning an empty list.** A catalogue that comes back
    empty is indistinguishable from a catalogue that is empty because the install
    lost its data, and the second case is a packaging fault the caller cannot fix
    and must be told about. So the invariant is: *a list this returns is never
    empty*. ``skill_index`` still has an "No skills given to render" arm, but it
    is only reachable from a caller that passes an empty list in itself.
    """
    if directory is not None:
        root: Optional[str] = directory
        if not os.path.isdir(root):
            raise SkillError(
                f"no such skills directory: {root!r}. Pass a folder holding "
                f"'*{SKILL_SUFFIX}' files, or omit the argument to load the "
                f"skills shipped with the package ({_PACKAGED_SKILLS})."
            )
    else:
        root = find_skills_dir()
        if root is None:
            raise SkillError(
                "no skills folder exists. Looked for "
                f"{_PACKAGED_SKILLS} (where [tool.setuptools.package-data] "
                f"ships them) and in every parent of {_HERE}. A wheel built "
                "without that glob installs the modules and none of the data, "
                "which is what this error reports - it is not an empty "
                "catalogue."
            )
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
    if not out:
        raise SkillError(
            f"{root} holds no '*{SKILL_SUFFIX}' files. It is the right folder "
            "but the data is missing from it, so treat this as a broken "
            "install rather than a catalogue with nothing in it."
        )
    return out


def skill_index(skills: Optional[List[Skill]] = None) -> str:
    """Render the catalogue. Deliberately without bodies, to stay cheap."""
    entries = load_skills() if skills is None else skills
    if not entries:
        # Unreachable through `load_skills()`, which raises instead. Kept for a
        # caller that hands an empty list in directly, because rendering that as
        # an empty `<available_skills></available_skills>` block would read as
        # "there are none anywhere" rather than "you gave me none".
        return (
            "No skills given to render. load_skills() raises rather than "
            "returning an empty catalogue; expected a 'skills' folder beside "
            "the package at %s." % _PACKAGED_SKILLS
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
