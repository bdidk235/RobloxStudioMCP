"""Data types exchanged with an MCP server."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Tool:
    """A single tool advertised by the MCP server."""

    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    #: Whether the tool only reads. Advertised as MCP ``annotations.readOnlyHint``,
    #: which clients use to decide whether a call may run in parallel with others.
    #: Defaults to ``False``: an unmarked tool is treated as mutating, which is the
    #: safe direction to be wrong in.
    read_only: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tool":
        annotations = data.get("annotations") or {}
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}) or {},
            read_only=bool(annotations.get("readOnlyHint", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.read_only:
            out["annotations"] = {"readOnlyHint": True}
        return out

    @property
    def properties(self) -> Dict[str, Any]:
        """The tool's input properties (name -> JSON Schema)."""
        return self.input_schema.get("properties", {}) or {}

    @property
    def required(self) -> List[str]:
        """The tool's required input parameter names."""
        return list(self.input_schema.get("required", []) or [])

    def has_parameter(self, name: str) -> bool:
        """Whether the tool's input schema declares ``name``."""
        return name in self.properties

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Tool(name={self.name!r})"


def _extract_balanced(text: str) -> Optional[str]:
    """Return the first balanced {...} or [...] prefix of ``text``, if any.

    Respects JSON strings and escapes so braces inside strings don't break
    the depth count. Returns ``None`` when brackets never balance.
    """
    if not text or text[0] not in ("{", "["):
        return None
    pairs = {"{": "}", "[": "]"}
    closing = pairs[text[0]]
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in ("{", "["):
            # Only count brackets matching the outer type's family? Count all
            # to stay balanced for nested structures like {"a": [1]}.
            depth += 1
        elif ch in ("}", "]"):
            depth -= 1
            if depth == 0:
                # Ensure the outer type closes correctly.
                if ch == closing:
                    return text[: i + 1]
                return None
    return None


@dataclass
class CallToolResult:
    """The result of a ``tools/call`` request."""

    content: List[Any] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CallToolResult":
        # Accept both the MCP-spec `isError` and the `is_error` alias kept
        # for interop with the TypeScript server (which sends both).
        return cls(
            content=data.get("content", []) or [],
            is_error=bool(data.get("isError", data.get("is_error", False))),
        )

    def text(self) -> str:
        """Concatenate all ``text`` content blocks into a single string."""
        parts: List[str] = []
        for block in self.content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)

    def json(self) -> Optional[Any]:
        """Best-effort parse of the text content as JSON.

        Handles plain JSON, JSON wrapped in Markdown code fences, and JSON
        embedded in surrounding prose. Returns ``None`` when nothing parses.
        """
        text = self.text().strip()
        if not text:
            return None

        # Strip Markdown code fences if present (```json ... ``` or ``` ... ```).
        if text.startswith("```"):
            lines = text.splitlines()
            # Drop opening fence (``` or ```json).
            lines = lines[1:]
            # Drop trailing fence if present.
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fall back to extracting the first balanced object/array, trying
        # each candidate start position in order (handles nested JSON and
        # multiple candidates in prose).
        for i, ch in enumerate(text):
            if ch not in ("{", "["):
                continue
            candidate = _extract_balanced(text[i:])
            if candidate is None:
                continue
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        preview = self.text()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        return f"CallToolResult(is_error={self.is_error!r}, text={preview!r})"
