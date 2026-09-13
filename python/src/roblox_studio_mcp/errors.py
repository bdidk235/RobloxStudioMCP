"""Exceptions raised by the :mod:`roblox_studio_mcp` package."""

from __future__ import annotations

from typing import Any, Optional


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
        super().__init__(f"[{code}] {message}")

    @classmethod
    def from_dict(cls, error: dict) -> "JSONRPCError":
        return cls(
            code=error.get("code", -1),
            message=error.get("message", "Unknown JSON-RPC error"),
            data=error.get("data"),
        )
