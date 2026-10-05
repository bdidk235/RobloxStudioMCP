"""A minimal, dependency-free MCP client over the stdio transport.

The Model Context Protocol speaks JSON-RPC 2.0 over stdin/stdout, with one
newline-delimited JSON message per line.  This module implements just enough of
that protocol to be useful: connect + initialize handshake, list tools, and
call tools.  Server-to-client requests are answered with a ``Method not found``
error so the server never blocks waiting on us.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence

from ._version import __version__
from .errors import JSONRPCError, MCPConnectionError, MCPToolError
from .types import CallToolResult, Tool

DEFAULT_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 120.0
_READ_CHUNK_SIZE = 65536


async def _readline_unbounded(
    stream: "asyncio.StreamReader",
    buf: bytearray,
) -> bytes:
    """Read one newline-terminated message without any size cap.

    ``StreamReader.readline()`` enforces its buffer ``limit`` (64KB by
    default) and raises ``LimitOverrunError`` on large payloads such as
    base64 screenshots. MCP stdio framing is one JSON value per line, so
    instead accumulate fixed-size ``read()`` chunks — which are not subject
    to the line limit — until a newline is seen. Memory use is exactly one
    message; there is no artificial cap to tune.
    """
    while True:
        idx = buf.find(b"\n")
        if idx != -1:
            line = bytes(buf[: idx + 1])
            del buf[: idx + 1]
            return line
        chunk = await stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            line = bytes(buf)
            buf.clear()
            return line
        buf += chunk


class MCPClient:
    """An async client for a single stdio MCP server.

    Parameters
    ----------
    command:
        The executable to launch (e.g. ``"cmd.exe"``, ``"npx"``, ``"node"``).
    args:
        Arguments passed to ``command``.
    env:
        Optional environment variable overrides (merged over ``os.environ``).
    cwd:
        Optional working directory for the child process.
    timeout:
        Per-request timeout in seconds.
    protocol_version:
        MCP protocol version advertised during the handshake.
    expand_env:
        When ``True`` (default), ``%VAR%``/``$VAR`` references in ``command``
        and ``args`` are expanded via :func:`os.path.expandvars` before launch.
    """

    def __init__(
        self,
        command: str,
        args: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        expand_env: bool = True,
        shell: bool = False,
        disabled_tools: Optional[Iterable[str]] = None,
    ) -> None:
        self._shell = shell
        # Declared, not inferred. These are mutually exclusive Optionals
        # discriminated by `_shell`, and inference from the first assignment
        # made each branch's other value a type error - five findings that all
        # came from one missing declaration. No checker can narrow the pair
        # through `if self._shell:`, so the invariant is stated at the use sites
        # instead of being left implicit.
        self._shell_cmd: Optional[str] = None
        self._argv: Optional[List[str]] = None
        if shell:
            self._shell_cmd = " ".join([command] + list(args or []))
        else:
            argv = [command] + list(args or [])
            if expand_env:
                argv = [os.path.expandvars(a) for a in argv]
            self._argv = argv

        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        self._env = full_env
        self._cwd = cwd
        self.timeout = timeout
        self.protocol_version = protocol_version

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        # Per-request deadline state, so a progress notification can push the
        # deadline out instead of the whole call dying on a flat timer.
        self._deadlines: Dict[int, Dict[str, float]] = {}
        #: Called with (request_id, progress_dict) for each progress notification.
        self.on_progress: Optional[Callable[[int, Dict[str, Any]], None]] = None
        self._send_lock = asyncio.Lock()
        self._stdout_buf = bytearray()
        self._stderr_buf = bytearray()
        self._next_id = 0
        self._initialized = False
        self._stderr_lines: Deque[str] = deque(maxlen=1000)
        self._disabled_tools: set = set(disabled_tools or ())

        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    async def connect(self) -> "MCPClient":
        """Spawn the server process and perform the initialize handshake."""
        if self._proc is not None:
            return self

        try:
            if self._shell:
                if self._shell_cmd is None:
                    raise MCPConnectionError("internal: shell mode with no shell command")
                self._proc = await asyncio.create_subprocess_shell(
                    self._shell_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env,
                    cwd=self._cwd,
                )
            else:
                if self._argv is None:
                    raise MCPConnectionError("internal: exec mode with no argv")
                self._proc = await asyncio.create_subprocess_exec(
                    *self._argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env,
                    cwd=self._cwd,
                )
        except FileNotFoundError as exc:
            target = self._argv[0] if self._argv else self._shell_cmd
            raise MCPConnectionError(
                f"Could not launch MCP server: {target!r} not found. "
                f"Full command: {self._shell_cmd if self._shell else ' '.join(self._argv or [])}"
            ) from exc

        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        result = await self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "roblox-studio-mcp", "version": __version__},
            },
        )
        self.server_info = result.get("serverInfo", {}) or {}
        self.capabilities = result.get("capabilities", {}) or {}
        # Adopt the server's negotiated protocol version if it sent one.
        self.protocol_version = result.get("protocolVersion", self.protocol_version)

        await self._notify("notifications/initialized", {})
        self._initialized = True
        return self

    async def close(self) -> None:
        """Terminate the server process and clean up background tasks."""
        proc = self._proc
        if proc is None:
            return

        # Close our write end first so the server observes EOF and can exit.
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (ConnectionResetError, BrokenPipeError, RuntimeError):
                pass

        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

        # Let the reader tasks observe EOF and finish naturally. Cancelling them
        # while they block on readline() leaks the pipe transports on Windows.
        for task in (self._read_task, self._stderr_task):
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()

        self._proc = None
        self._initialized = False

    async def wait_closed(self) -> None:
        """Wait until the child process has fully exited."""
        if self._proc is not None:
            await self._proc.wait()

    async def __aenter__(self) -> "MCPClient":
        return await self.connect()

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ #
    # High-level MCP operations
    # ------------------------------------------------------------------ #
    async def list_tools(self) -> List[Tool]:
        """Return the tools advertised by the server (disabled tools omitted)."""
        result = await self._request("tools/list", {})
        tools = [Tool.from_dict(t) for t in (result.get("tools") or [])]
        return [t for t in tools if t.name not in self._disabled_tools]

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> CallToolResult:
        """Call a tool by name and return its result.

        Raises
        ------
        MCPToolError
            If the tool is disabled via ``disabled_tools``.
        """
        if name in self._disabled_tools:
            raise MCPToolError(f"Tool {name!r} is disabled.")
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        parsed = CallToolResult.from_dict(result)
        if parsed.is_error:
            raise MCPToolError(f"Tool {name!r} reported an error: {parsed.text()}")
        return parsed

    async def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a raw JSON-RPC request and return the ``result`` dict.

        Unlike :meth:`list_tools` and :meth:`call_tool`, this does not parse,
        filter, or raise on tool errors — it returns the server's raw response
        so callers (such as the stdio proxy) can forward it verbatim.
        """
        return await self._request(method, params)

    # ------------------------------------------------------------------ #
    # Protocol primitives
    # ------------------------------------------------------------------ #
    async def _request(self, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        self._require_connected()
        request_id = self._new_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        # `deadline` is separate from `future` so a progress notification can
        # push it out. A single asyncio.wait_for cannot express "extend when the
        # server shows signs of life", which is the whole point here.
        state: Dict[str, float] = {"deadline": loop.time() + self.timeout}
        self._pending[request_id] = future
        self._deadlines[request_id] = state

        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        # Echo the id as the progress token so a server that supports progress
        # can extend our deadline, and so we can attribute it back to this call.
        message["params"] = dict(params or {})
        message["params"].setdefault(
            "_meta", {"progressToken": request_id}
        )

        await self._send(message)
        try:
            while True:
                remaining = state["deadline"] - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    if future.done():
                        # The shield was cancelled, not the future: a real
                        # result landed in the same tick the timer fired.
                        return future.result()
                    if loop.time() >= state["deadline"]:
                        raise
                    # A progress notification extended the deadline; keep going.
        except asyncio.TimeoutError:
            raise MCPConnectionError(
                f"Timed out waiting for {method!r} after {self.timeout}s "
                "(no progress notification arrived to extend it)"
            ) from None
        finally:
            self._pending.pop(request_id, None)
            self._deadlines.pop(request_id, None)

    async def _notify(self, method: str, params: Optional[Dict[str, Any]]) -> None:
        self._require_connected()
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: Dict[str, Any]) -> None:
        proc = self._require_connected()
        line = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        # `Process.stdin` is Optional in typeshed because a process need not have
        # one. Every spawn here passes PIPE, so this is an invariant rather than a
        # real possibility - but stating it converts a latent AttributeError into a
        # message that says what actually went wrong.
        if proc.stdin is None:
            raise MCPConnectionError("internal: subprocess has no stdin pipe")
        async with self._send_lock:
            proc.stdin.write((line + "\n").encode("utf-8"))
            await proc.stdin.drain()

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _require_connected(self) -> asyncio.subprocess.Process:
        if self._proc is None or self._proc.stdin is None:
            raise MCPConnectionError("Not connected. Call `await client.connect()` first.")
        return self._proc

    # ------------------------------------------------------------------ #
    # Background I/O
    # ------------------------------------------------------------------ #
    async def _read_loop(self) -> None:
        proc = self._require_connected()
        assert proc.stdout is not None
        try:
            while True:
                raw = await _readline_unbounded(proc.stdout, self._stdout_buf)
                if not raw:
                    break  # EOF: server exited
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    # Skip one malformed line; don't kill all pending requests.
                    # Stderr buffer retains context for debugging via stderr_lines.
                    self._stderr_lines.append(f"Skipped non-JSON stdout line: {line[:200]}")
                    continue
                if not isinstance(message, dict):
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail every pending request
            self._fail_all_pending(exc)
        finally:
            self._fail_all_pending(
                MCPConnectionError("MCP server connection closed unexpectedly")
            )

    async def _dispatch(self, message: Dict[str, Any]) -> None:
        has_id = "id" in message
        has_method = "method" in message

        if has_id and has_method:
            # A request from the server to us. We implement none of them, but
            # we must reply so the server does not block waiting.
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {message.get('method')}",
                    },
                }
            )
            return

        if has_id:
            future = self._pending.get(message["id"])
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(JSONRPCError.from_dict(message["error"]))
            else:
                future.set_result(message.get("result"))
            return

        # A notification (no id).
        if message.get("method") == "notifications/progress":
            params = message.get("params") or {}
            token = params.get("progressToken")
            # Progress is proof of life: the server is working, so the deadline
            # moves rather than the call being abandoned while it still might
            # finish. Requests that opted in carry a token equal to their id.
            state = self._deadlines.get(token) if isinstance(token, int) else None
            if state is not None:
                state["deadline"] = asyncio.get_running_loop().time() + self.timeout
            if self.on_progress is not None and isinstance(token, int):
                try:
                    self.on_progress(token, params)
                except Exception:  # noqa: BLE001 - a progress hook must not break the call
                    pass
        return

    async def _drain_stderr(self) -> None:
        proc = self._require_connected()
        assert proc.stderr is not None
        while True:
            raw = await _readline_unbounded(proc.stderr, self._stderr_buf)
            if not raw:
                break
            self._stderr_lines.append(raw.decode("utf-8", errors="replace").rstrip())

    def _fail_all_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    @property
    def stderr_lines(self) -> List[str]:
        """The most recent stderr output from the server (for debugging)."""
        return list(self._stderr_lines)

    @property
    def is_connected(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self._initialized

    @property
    def disabled_tools(self) -> set:
        """The set of tool names this client refuses to expose or call."""
        return set(self._disabled_tools)
