"""A transparent stdio MCP server that proxies the Roblox Studio MCP.

Claude Code (or any MCP client) can point at this server instead of launching
StudioMCP directly.  It presents the *same* tools and raw responses as StudioMCP,
but routes every request through :class:`~roblox_studio_mcp.client.MCPClient` so
the connection is owned and managed by this package — a single upstream process,
reused for the server's whole lifetime.

Launch it as a stdio server::

    python -m roblox_studio_mcp.server
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, Iterable, Optional

from .client import MCPClient
from .roblox import default_args, default_command, default_shell


async def serve(
    client: Optional[MCPClient] = None,
    disabled_tools: Optional[Iterable[str]] = None,
) -> None:
    """Run the stdio proxy until stdin closes.

    A connected ``client`` may be supplied (useful for tests); otherwise the
    default Studio MCP launch command is used and connected eagerly.

    ``disabled_tools`` names tools to hide from ``tools/list`` and refuse on
    ``tools/call``. It only applies when this function constructs the client; a
    supplied client carries its own set (``MCPClient(..., disabled_tools=...)``)
    and that is the one used.
    """
    owns_client = client is None
    if client is None:
        client = MCPClient(
            default_command(),
            default_args(),
            shell=default_shell(),
            disabled_tools=disabled_tools,
        )
        await client.connect()

    try:
        await _relay_loop(client)
    finally:
        if owns_client:
            await client.close()


async def _relay_loop(client: MCPClient) -> None:
    loop = asyncio.get_running_loop()
    stdin = sys.stdin.buffer
    while True:
        raw = await loop.run_in_executor(None, stdin.readline)
        if not raw:
            break  # stdin closed
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        await _handle_message(client, message)


async def _handle_message(client: MCPClient, message: Dict[str, Any]) -> None:
    if "method" not in message:
        return  # not a request/notification; ignore

    method = message.get("method")
    has_id = "id" in message

    if method == "initialize":
        if has_id:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": client.protocol_version,
                        "capabilities": client.capabilities,
                        "serverInfo": client.server_info,
                    },
                }
            )
        return

    if method in ("notifications/initialized", "notifications/cancelled"):
        return  # notifications: no response

    if method == "ping":
        if has_id:
            _send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
        return

    if method == "tools/list":
        params = message.get("params") or {}
        try:
            result = await client.request(method, params)
        except Exception as exc:  # noqa: BLE001 - surface as a JSON-RPC error
            if has_id:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32000, "message": str(exc)},
                    }
                )
            return
        if has_id:
            # Honor client-side disabled_tools even though we proxy raw.
            disabled: set = getattr(client, "disabled_tools", set()) or set()
            if disabled and isinstance(result, dict) and isinstance(result.get("tools"), list):
                result = {
                    **result,
                    "tools": [t for t in result["tools"] if t.get("name") not in disabled],
                }
            _send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        return

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") if isinstance(params, dict) else None
        disabled = getattr(client, "disabled_tools", set()) or set()
        if name in disabled:
            if has_id:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32602, "message": f"Tool {name!r} is disabled."},
                    }
                )
            return
        try:
            result = await client.request(method, params)
        except Exception as exc:  # noqa: BLE001 - surface as a JSON-RPC error
            if has_id:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32000, "message": str(exc)},
                    }
                )
            return
        if has_id:
            _send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        return

    if has_id:
        _send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )


def _send(message: Dict[str, Any]) -> None:
    sys.stdout.buffer.write(
        (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
    )
    sys.stdout.buffer.flush()


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
