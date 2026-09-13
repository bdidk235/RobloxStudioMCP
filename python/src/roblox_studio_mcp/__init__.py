"""roblox-studio-mcp: a dependency-free Python client for MCP servers.

Built with the Roblox Studio MCP in mind, but the :class:`MCPClient` is a
generic stdio MCP client that works with any server.
"""

from __future__ import annotations

from ._version import __version__
from .client import MCPClient
from .errors import (
    JSONRPCError,
    MCPConnectionError,
    MCPError,
    MCPProtocolError,
    MCPToolError,
)
from .roblox import (
    MACOS_COMMAND,
    WINDOWS_ARGS,
    WINDOWS_COMMAND,
    RobloxStudio,
    close_singleton,
    default_args,
    default_command,
    default_shell,
    get_singleton,
    platform_defaults,
)
from .types import CallToolResult, Tool

__all__ = [
    "MCPClient",
    "RobloxStudio",
    "CallToolResult",
    "Tool",
    "JSONRPCError",
    "MCPConnectionError",
    "MCPError",
    "MCPProtocolError",
    "MCPToolError",
    "get_singleton",
    "close_singleton",
    "default_args",
    "default_command",
    "default_shell",
    "platform_defaults",
    "MACOS_COMMAND",
    "WINDOWS_ARGS",
    "WINDOWS_COMMAND",
    "__version__",
]
