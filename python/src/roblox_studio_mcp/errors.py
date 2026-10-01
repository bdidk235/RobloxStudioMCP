"""Exceptions raised by the :mod:`roblox_studio_mcp` package."""

from __future__ import annotations

from typing import Any, Dict, Optional


class MCPError(Exception):
    """Base class for all errors raised by this package."""


class MCPConnectionError(MCPError):
    """Raised when the connection to an MCP server fails or drops."""


class MCPProtocolError(MCPError):
    """Raised when the server sends something that violates the protocol."""


class MCPToolError(MCPError):
    """Raised when a tool call fails.

    This covers two cases: the JSON-RPC layer reporting an error for the
    ``tools/call`` request, or the tool itself reporting ``isError=True``.
    """


class JSONRPCError(MCPError):
    """A JSON-RPC 2.0 error object returned by the server.

    Attributes mirror the JSON-RPC spec: ``code``, ``message`` and an optional
    ``data`` payload.

    ``code`` is the numeric JSON-RPC code, which for tool failures is always
    ``-32000`` and therefore useless for branching. The *stable* code is a
    string in ``data.code``; :attr:`tool_code` surfaces it so a caller can write
    ``except JSONRPCError as e: if e.tool_code == "NO_STUDIO"`` instead of
    matching on English.
    """

    def __init__(
        self,
        code: int,
        message: str,
        data: Optional[Any] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.data = data
        stable = self.tool_code
        # Shown in the string too, so a log line or a traceback is actionable
        # without the caller having to know to reach into `data`.
        prefix = f"{stable}: " if stable else ""
        super().__init__(f"[{code}] {prefix}{message}")

    @property
    def tool_code(self) -> Optional[str]:
        """The stable string code, when the server supplied one."""
        if isinstance(self.data, dict):
            value = self.data.get("code")
            if isinstance(value, str):
                return value
        return None

    @property
    def details(self) -> Dict[str, Any]:
        """Structured extras from the error, minus the code itself."""
        if isinstance(self.data, dict):
            return {k: v for k, v in self.data.items() if k != "code"}
        return {}

    @classmethod
    def from_dict(cls, error: dict) -> "JSONRPCError":
        return cls(
            code=error.get("code", -1),
            message=error.get("message", "Unknown JSON-RPC error"),
            data=error.get("data"),
        )
