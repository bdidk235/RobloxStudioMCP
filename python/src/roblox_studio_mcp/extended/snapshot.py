"""A bounded read of the live instance tree.

Why this exists
---------------
Something outside this repository needed to read the DataModel's instance
tree - to diff it over time, to know what a place contains - and the only
read tools on the table were a search (``search_game_tree``) and a
single-path inspection (``inspect_instance``). A search answers "where is X";
it does not answer "what is here". Reconstructing the tree by searching for
everything is not a design, it is a workaround that costs one call per node.

The point of this module is that a snapshot is a **bounded** read. A live tree
can be arbitrarily large, and an unbounded read is a payload bomb: the
``execute_luau`` return channel truncates at exactly 100,015 characters
**with no error** (measured; see ``extended/capture.py``), so an unbounded
walk returns half a tree that looks complete. Every bound here is therefore
enforced, and a result that would not fit is reported as too large rather
than returned half-read.

What it is not
--------------
**Not a diff.** The comparison belongs to whoever holds the previous
snapshot, not to a server that keeps no state. This module returns a
snapshot; the consumer diffs it. The seam is deliberately one-way, because
the alternative - a server that remembers the last tree - is a different
product with different lifetime and privacy obligations.

**Not a search.** Finding a path is ``search_game_tree``'s job. This tool
starts at a path the caller already has.

The path is spliced into generated Luau
---------------------------------------
The walk's root path is interpolated into the generated code, so it is
validated with the **shared** game-tree validator (``writer.validate_game_tree_path``)
before that happens. That validator is regex-only - dot-separated Luau
identifiers - which is what makes the splice safe. It was written after a
PoC-measured injection: a ``target_path`` containing a newline landed
caller-controlled lines as executed code in the Editor, because the
container half was spliced raw. A second path validator would be a second
thing to get wrong, so there is one, and the bare root ``game`` is the one
case it does not cover, handled here.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .errors import INVALID_ARGUMENT, LUA_ERROR, NOT_FOUND, SIZE_LIMIT, ToolError, describe
from .writer import validate_game_tree_path

#: Levels below the root to include by default. 1 would be direct children
#: only; 3 is enough to show a part's children and their children, which is
#: the shape a consumer diffing over time actually wants.
DEFAULT_DEPTH = 3

#: Hard ceiling on depth. A path deeper than this is a different tool.
MAX_DEPTH = 10

#: Total nodes returned by default, the root included.
DEFAULT_NODES = 200

#: Hard ceiling on nodes. At roughly 60 characters per line, 1,000 nodes is
#: about 60 KB - inside the return channel with room to spare, which is the
#: point: the bound has to be safe *before* the fact, because the channel
#: truncates without complaint.
MAX_NODES = 1000

#: The ``execute_luau`` return channel truncates at exactly 100,015
#: characters with no error. A snapshot that reaches it has lost its trailer,
#: which is how the truncation is detected (see :func:`parse_snapshot`).
RETURN_CHANNEL_LIMIT = 100_015

#: The last line of a successful walk. It cannot be produced by a node line:
#: node lines start with a path, and the shortest possible path is the root
#: itself, so a line consisting only of this token is unambiguous.
_TRAILER = "__END__"

#: ``\\`` escapes a backslash, ``\\<digits>`` a control character. Instance
#: names may contain any character, including the tab this format uses as a
#: field separator, so the path is escaped on the way out and unescaped on
#: the way in.
_ESCAPE_RE = re.compile(r"\\(\\|[0-9]{1,3})")

#: The Luau that performs the walk. Placeholders are filled by
#: :func:`build_walk` with ``str.replace``, not ``%``-formatting, because the
#: source contains a literal ``%`` (the Lua pattern class ``%c``) and a
#: literal backslash, and a format string would need both escaped correctly
#: for no benefit.
_LUAU_TEMPLATE = r'''
local MAX_NODES = __NODES__
local MAX_DEPTH = __DEPTH__
local function esc(s)
  return (tostring(s):gsub("[\\%c]", function(c)
    if c == "\\" then return "\\\\" end
    return "\\" .. string.byte(c)
  end))
end
-- The per-instance identity. `GetDebugId()` is readable for every instance
-- and is stable across a rename and a reparent (measured 2026-10-20), which
-- is what a diff pairs on. It is NOT the file's UniqueId: that read is
-- capability-gated for EVERY instance ("lacking capability RobloxScript"),
-- so the name here says which id this is rather than borrowing the other
-- one's. A read that throws must not break the walk, so it is guarded and
-- an unreadable id is reported as empty rather than aborting the snapshot.
local function localId(inst)
  local ok, v = pcall(function() return inst:GetDebugId() end)
  if ok and type(v) == "string" then return v end
  return ""
end
local function resolve(spec)
  local cur = game
  local prefix = "game"
  for part in spec:gmatch("[^%.]+") do
    if part ~= "game" then
      local nxt = cur:FindFirstChild(part)
      if nxt == nil then return nil, nil end
      cur = nxt
      prefix = prefix .. "." .. esc(nxt.Name)
    end
  end
  return cur, prefix
end
local start, rootPath = resolve("__PATH__")
if start == nil then return "ERR\tNOT_FOUND" end
local lines = {}
local count = 0
local truncated = false
local depthLimited = false
local function walk(kids, depth, prefix)
  local n = #kids
  for i = 1, n do
    if count >= MAX_NODES then
      truncated = true
      return
    end
    local child = kids[i]
    local ck = child:GetChildren()
    local p = prefix .. "." .. esc(child.Name)
    count = count + 1
    lines[#lines + 1] = p .. "\t" .. esc(child.ClassName) .. "\t" .. #ck .. "\t" .. localId(child)
    if depth >= MAX_DEPTH then
      if #ck > 0 then depthLimited = true end
    else
      walk(ck, depth + 1, p)
    end
  end
end
local rootKids = start:GetChildren()
count = 1
lines[1] = rootPath .. "\t" .. esc(start.ClassName) .. "\t" .. #rootKids .. "\t" .. localId(start)
walk(rootKids, 1, rootPath)
return table.concat(lines, "\n") .. "\n__END__\t" .. count .. "\t" .. tostring(truncated) .. "\t" .. tostring(depthLimited)
'''


def unescape(text: str) -> str:
    """Undo the escaping :func:`build_walk`'s ``esc`` applies.

    ``\\`` is a backslash and ``\\<digits>`` is the character with that byte
    value. The digits are the *byte*, not the code point, so a non-ASCII
    name survives unchanged (it is never escaped in the first place - only
    control characters and backslashes are).
    """
    return _ESCAPE_RE.sub(
        lambda m: "\\" if m.group(1) == "\\" else chr(int(m.group(1))), text
    )


def validate_root_path(path: str) -> str:
    """Validate the walk's root, allowing the bare DataModel root.

    The shared validator requires at least one segment after ``game``; the
    root itself is a legitimate walk root, so it is the one case handled
    here rather than by loosening the shared regex for everybody.

    Args:
        path: The caller-supplied path. Not trusted.

    Returns:
        The path, unchanged.

    Raises:
        ToolError: ``INVALID_ARGUMENT`` when the path is not a string, is
            not rooted at ``game``, or contains anything but dot-separated
            Luau identifiers.
    """
    if not isinstance(path, str) or not path:
        raise ToolError(
            INVALID_ARGUMENT,
            "path must be a non-empty string like 'game.Workspace'; got %s."
            % describe(path),
        )
    if path == "game":
        return path
    return validate_game_tree_path(path, field="path")


def bounded_int(
    value: Any, field: str, low: int, high: int, default: int
) -> int:
    """Coerce a bound, refusing anything that is not a plain integer.

    ``int("5")`` would accept a string the schema declares an integer, and
    ``True`` is an ``int`` in Python - so a bool is refused rather than
    silently becoming 1. The range is enforced here, so the generated Luau
    cannot be handed a negative depth or a node cap of a billion.

    Raises:
        ToolError: ``INVALID_ARGUMENT`` when the value is not an integer in
            range.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(
            INVALID_ARGUMENT,
            "%s must be an integer between %d and %d; got %s."
            % (field, low, high, describe(value)),
        )
    if value < low or value > high:
        raise ToolError(
            INVALID_ARGUMENT,
            "%s must be between %d and %d; got %d." % (field, low, high, value),
        )
    return value


def build_walk(root_path: str, max_depth: int, max_nodes: int) -> str:
    """Build the Luau that walks the tree and returns it as delimited text.

    The result is tab-separated, one node per line:
    ``path<TAB>className<TAB>childCount``, then a trailer line carrying the
    count and the two flags. The trailer is what makes a truncated return
    detectable - see :func:`parse_snapshot`.

    Args:
        root_path: An already-validated path (see :func:`validate_root_path`).
        max_depth: Levels below the root to include.
        max_nodes: Total nodes to return, the root included.

    Returns:
        The Luau source.
    """
    return (
        _LUAU_TEMPLATE.replace("__NODES__", str(int(max_nodes)))
        .replace("__DEPTH__", str(int(max_depth)))
        .replace("__PATH__", root_path)
    )


def parse_snapshot(
    text: str,
) -> Tuple[List[Dict[str, Any]], int, bool, bool]:
    """Parse the walk's output into nodes and flags.

    Args:
        text: The raw return value of the walk.

    Returns:
        ``(nodes, count, truncated, depth_limited)``.

    Raises:
        ToolError: ``SIZE_LIMIT`` when the trailer is missing - the return
            channel truncated and the tree is incomplete, which must not be
            handed back as a complete-looking snapshot. ``LUA_ERROR`` when a
            node line does not have the expected shape.
    """
    lines = [line for line in text.split("\n") if line != ""]
    if not lines or not lines[-1].startswith(_TRAILER + "\t"):
        raise ToolError(
            SIZE_LIMIT,
            "the snapshot did not fit the execute_luau return channel "
            "(it truncates at %d characters with no error), so the tree "
            "returned is incomplete. Lower max_nodes or max_depth, or read "
            "one subtree at a time." % RETURN_CHANNEL_LIMIT,
        )
    fields = lines[-1].split("\t")
    try:
        count = int(fields[1])
        truncated = fields[2] == "true"
        depth_limited = fields[3] == "true"
    except (IndexError, ValueError):
        raise ToolError(
            LUA_ERROR,
            "the walk's trailer line is malformed: %r" % lines[-1],
        )
    nodes: List[Dict[str, Any]] = []
    for line in lines[:-1]:
        parts = line.split("\t")
        if len(parts) not in (3, 4):
            raise ToolError(
                LUA_ERROR,
                "the walk returned a line that is not path/class/count/id: %r"
                % line,
            )
        path, class_name, children = parts[0], parts[1], parts[2]
        # A three-field line is an older walk: the id was not read then, and
        # an absent id is reported as absent rather than invented.
        debug_id = parts[3] if len(parts) == 4 else ""
        try:
            child_count = int(children)
        except ValueError:
            raise ToolError(
                LUA_ERROR,
                "the walk returned a non-numeric child count %r for %r"
                % (children, path),
            )
        nodes.append(
            {
                "path": unescape(path),
                "className": unescape(class_name),
                "children": child_count,
                "debugId": unescape(debug_id),
            }
        )
    return nodes, count, truncated, depth_limited


async def snapshot(
    studio: Any,
    path: str = "game",
    max_depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_NODES,
    datamodel_type: str = "Edit",
) -> Dict[str, Any]:
    """Read the instance tree under ``path``, bounded.

    Args:
        studio: A connected :class:`~roblox_studio_mcp.roblox.RobloxStudio`.
        path: The root of the walk. Validated before it reaches any code.
        max_depth: Levels below the root to include.
        max_nodes: Total nodes to return, the root included.
        datamodel_type: The DataModel to read. The edit-time tree is the
            default: it is the tree a place file is made of, and the one a
            consumer diffing over time wants to see change.

    Returns:
        A report dict: ``root``, ``nodes`` (each with ``path``,
        ``className``, ``children``), ``count``, ``truncated``,
        ``depth_limited``, the bounds that were applied, and ``bytes``
        (the size of the raw return, so a consumer can see how close to the
        channel limit it ran).

    Raises:
        ToolError: ``INVALID_ARGUMENT`` for a bad path or bound,
            ``NOT_FOUND`` when the path does not resolve, ``SIZE_LIMIT`` when
            the result would not fit the return channel, ``LUA_ERROR`` when
            the walk's output is malformed.
    """
    root = validate_root_path(path)
    depth = bounded_int(max_depth, "max_depth", 1, MAX_DEPTH, DEFAULT_DEPTH)
    nodes_cap = bounded_int(max_nodes, "max_nodes", 1, MAX_NODES, DEFAULT_NODES)

    result = await studio.execute_luau(
        build_walk(root, depth, nodes_cap), datamodel_type=datamodel_type
    )
    text = result.text()
    if text.startswith("ERR\t"):
        raise ToolError(
            NOT_FOUND,
            "no instance at %r. The path is case-insensitive in Studio but "
            "must name an existing child of its parent." % root,
            path=root,
        )
    nodes, count, truncated, depth_limited = parse_snapshot(text)
    with_id = sum(1 for node in nodes if node.get("debugId"))
    return {
        "root": root,
        "nodes": nodes,
        "count": count,
        "truncated": truncated,
        "depth_limited": depth_limited,
        "max_depth": depth,
        "max_nodes": nodes_cap,
        "datamodel_type": datamodel_type,
        "bytes": len(text),
        "nodes_with_id": with_id,
        "nodes_without_id": len(nodes) - with_id,
        "note": (
            "Node paths are the names Studio reports, so one may contain a "
            "space (a service called 'Run Service' does). An input path must "
            "be identifier-only, so such a name cannot be addressed as a root "
            "here - use search_game_tree to find it. The per-instance id is "
            "'debugId': it is stable across a rename and a reparent, so a "
            "diff can pair on identity rather than on position. It is NOT the "
            "file's UniqueId - that read is capability-gated for every "
            "instance under execute_luau (measured), so it exists only in a "
            "saved place file. This id is scoped to this Studio session."
        ),
    }


def describe_flags(truncated: bool, depth_limited: bool) -> str:
    """One line saying what was left out, or that nothing was.

    The flags in the report are booleans, which a consumer has to interpret.
    This is the sentence that says it instead, and it is deliberately
    explicit: "truncated" alone reads as "there is more", not as "here is
    what you are missing".
    """
    if truncated and depth_limited:
        return (
            "incomplete: the node cap and the depth cap both cut the walk, "
            "so nodes are missing at the cap and deeper ones were not visited"
        )
    if truncated:
        return "incomplete: the node cap cut the walk, so more nodes exist below"
    if depth_limited:
        return (
            "incomplete: the depth cap stopped the descent, so deeper "
            "children were not visited"
        )
    return "complete within the requested bounds"
