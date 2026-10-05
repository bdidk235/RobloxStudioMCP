"""Convenience layer for the Roblox Studio MCP server.

The Studio MCP proxy is launched by ``cmd.exe /c %LOCALAPPDATA%\\Roblox\\mcp.bat``
and exposes a set of tools, almost all of which require a ``studio_id``
identifying the target Studio instance (obtained from ``list_roblox_studios``).
This module wraps :class:`~roblox_studio_mcp.client.MCPClient` so that the ``studio_id``
is resolved once and injected automatically into every tool call that needs it.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .client import MCPClient
from .errors import MCPToolError
from .types import CallToolResult, Tool

# The Studio MCP proxy launcher on Windows. The `cd /d ... && .\mcp.bat` form is
# what Roblox's own configuration uses; it runs the proxy from the Roblox data
# directory, which the proxy relies on to locate the running Studio instance.
WINDOWS_COMMAND = "cmd.exe"
WINDOWS_ARGS: Tuple[str, ...] = ("/c", r'"cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat"')

# Studio MCP proxy binary inside the macOS Studio app bundle (no shell
# wrapper needed — it speaks stdio directly).
# See https://create.roblox.com/docs/studio/mcp
MACOS_COMMAND = "/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP"


def platform_defaults(platform: Optional[str] = None) -> Tuple[str, List[str], bool]:
    """Launch settings for the Studio MCP proxy, selected by platform.

    macOS runs the bundled ``StudioMCP`` binary directly; every other
    platform keeps the Windows ``cmd.exe /c …mcp.bat`` default (pass
    explicit ``command``/``args``/``shell`` to override on any platform).
    Returns ``(command, args, shell)``.
    """
    if platform is None:
        platform = sys.platform
    if platform == "darwin":
        return MACOS_COMMAND, [], False
    return WINDOWS_COMMAND, list(WINDOWS_ARGS), True

# How long resolve_studio_id() rides through a fresh proxy whose Studio uplink
# is not usable yet before giving up. A new proxy answers list_roblox_studios
# with "Unable to reach Roblox Studio" for a beat after its handshake.
RESOLVE_TIMEOUT = 10.0
RESOLVE_INTERVAL = 0.2

# Matches the proxy's transient not-ready symptom (fresh proxy, uplink warming).
_NOT_READY_HINT = "unable to reach"

# Keys under which list_roblox_studios has been observed to carry its array.
_STUDIO_LIST_KEYS = ("studios", "instances", "data", "result")

# The proxy's symptom for a studio_id that names a Studio it can no longer
# reach: the instance closed, or its place unloaded. Distinct from the
# not-ready hint above, which means the whole uplink is still warming.
_STALE_ID_HINTS = ("is not connected", "place is not open")


def _is_not_ready_error(exc: BaseException) -> bool:
    """Whether ``exc`` is the proxy's transient uplink-warming symptom."""
    return isinstance(exc, MCPToolError) and _NOT_READY_HINT in str(exc).lower()


def _is_stale_studio_id_error(exc: BaseException) -> bool:
    """Whether ``exc`` says the targeted Studio instance is no longer reachable."""
    if not isinstance(exc, MCPToolError):
        return False
    message = str(exc).lower()
    return any(hint in message for hint in _STALE_ID_HINTS)


class RobloxStudio:
    """A high-level client tuned for the Roblox Studio MCP server.

    Example
    -------
    .. code-block:: python

        import asyncio
        from roblox_studio_mcp import RobloxStudio

        async def main():
            async with await RobloxStudio.connect() as studio:
                tools = await studio.list_tools()
                print([t.name for t in tools])

                result = await studio.call(
                    "execute_luau",
                    {"code": "return 1 + 1", "datamodel_type": "Edit"},
                )
                print(result.text())

        asyncio.run(main())
    """

    def __init__(
        self,
        client: MCPClient,
        studio_id: Optional[str] = None,
        *,
        _is_singleton: bool = False,
    ) -> None:
        self._client = client
        self._studio_id = studio_id
        self._tools: Dict[str, Tool] = {}
        self._is_singleton = _is_singleton

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    async def connect(
        cls,
        studio_id: Optional[str] = None,
        *,
        command: Optional[str] = None,
        args: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 120.0,
        shell: Optional[bool] = None,
        disabled_tools: Optional[Iterable[str]] = None,
        singleton: bool = True,
    ) -> "RobloxStudio":
        """Launch the Studio MCP proxy and connect to it.

        Parameters
        ----------
        studio_id:
            The Studio instance to target. If omitted, it is resolved on each
            tool call that needs it via ``list_roblox_studios``, and only
            accepted when exactly one Studio is connected. With more than one
            connected the call raises rather than guessing; pass ``studio_id``
            explicitly, or per call, to choose.
        disabled_tools:
            Names of tools to hide from ``list_tools`` and refuse to call.
        singleton:
            When ``True`` (default), return the process-wide shared connection
            (see :func:`get_singleton`) instead of launching a fresh proxy.
            Pass ``False`` for an isolated connection.
        command, args, env, timeout, shell:
            Passed through to :class:`~roblox_studio_mcp.client.MCPClient`.
            ``command``/``args``/``shell`` default per platform
            (:func:`platform_defaults` — Windows ``cmd.exe …mcp.bat`` in
            shell mode, macOS ``StudioMCP`` binary directly); explicit values
            always win.
        """
        _command = command if command is not None else default_command()
        _args = list(args) if args is not None else default_args()
        _shell = shell if shell is not None else default_shell()
        if singleton:
            return await get_singleton(
                studio_id=studio_id,
                command=_command,
                args=_args,
                env=env,
                timeout=timeout,
                shell=_shell,
                disabled_tools=disabled_tools,
            )

        client = MCPClient(
            _command,
            _args,
            env=env,
            timeout=timeout,
            shell=_shell,
            disabled_tools=disabled_tools,
        )
        await client.connect()
        return cls(client, studio_id)

    @property
    def client(self) -> MCPClient:
        """The underlying generic :class:`MCPClient`."""
        return self._client

    @property
    def studio_id(self) -> Optional[str]:
        """The explicitly configured ``studio_id``, or ``None``.

        An implicitly resolved id is never stored here, so this stays
        ``None`` unless the caller pinned a Studio via ``connect()`` or
        :meth:`set_studio_id`.
        """
        return self._studio_id

    def set_studio_id(self, studio_id: Optional[str]) -> None:
        """Point this client at a different Studio instance (public setter)."""
        self._studio_id = studio_id

    def invalidate_cache(self) -> None:
        """Forget cached ``list_tools`` results so the next call re-fetches."""
        self._tools.clear()

    async def close(self) -> None:
        """Close the connection to the Studio MCP proxy.

        If this instance is the process-wide singleton, the singleton is also
        forgotten so a later :func:`get_singleton` call starts fresh.
        """
        await self._client.close()
        global _singleton
        if _singleton is self:
            _singleton = None

    async def __aenter__(self) -> "RobloxStudio":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # Don't close the shared process when used as
        # ``async with await RobloxStudio.connect()`` (singleton default).
        # Explicitly call ``await studio.close()`` or ``close_singleton()``.
        if self._is_singleton:
            return
        await self.close()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    async def list_tools(self, *, refresh: bool = False) -> List[Tool]:
        """Return all tools exposed by the Studio MCP server."""
        if refresh or not self._tools:
            tools = await self._client.list_tools()
            self._tools = {t.name: t for t in tools}
        return list(self._tools.values())

    async def get_tool(self, name: str) -> Tool:
        """Return a single tool by name (fetching the list if needed)."""
        if not self._tools:
            await self.list_tools()
        tool = self._tools.get(name)
        if tool is None:
            raise MCPToolError(
                f"Unknown tool {name!r}. Available: {sorted(self._tools)}"
            )
        return tool

    async def list_studios(self) -> List[Dict[str, Any]]:
        """Return the connected Studio instances as a list of dicts.

        An empty list means the proxy really reported no instances. A payload
        shape this client does not recognise raises instead, because the two
        are different faults: collapsing them reports schema drift as "no
        Studio is connected", which sends the caller to check the MCP toggle
        when the real problem is on this side of the wire.
        """
        result = await self._client.call_tool("list_roblox_studios", {})
        data = result.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in _STUDIO_LIST_KEYS:
                if key in data:
                    value = data[key]
                    if isinstance(value, list):
                        return value
                    raise MCPToolError(
                        f"list_roblox_studios returned {key!r} as "
                        f"{type(value).__name__}, expected a list. "
                        f"Keys present: {sorted(map(str, data))!r}."
                    )
            raise MCPToolError(
                "list_roblox_studios returned an unrecognised shape: a dict "
                f"with keys {sorted(map(str, data))!r} and none of "
                f"{list(_STUDIO_LIST_KEYS)!r}. This client needs updating; the "
                f"response was {data!r}"
            )
        raise MCPToolError(
            "list_roblox_studios returned an unrecognised shape: "
            f"{type(data).__name__}, expected a list or a dict. "
            f"The response was {data!r}"
        )

    async def resolve_studio_id(
        self,
        *,
        timeout: float = RESOLVE_TIMEOUT,
        interval: float = RESOLVE_INTERVAL,
    ) -> str:
        """Return the ``studio_id`` to use.

        An id configured by the caller (``connect(studio_id=...)`` or
        :meth:`set_studio_id`) is returned as-is and is never re-validated.

        Otherwise the id is resolved from ``list_roblox_studios`` on **every**
        call. It is accepted only when exactly one Studio is connected: zero
        raises, and more than one raises too, because list order is the proxy
        mesh's and carries no intent — a silently chosen Studio is how probes
        end up reading the wrong place. Pin the instance explicitly to
        disambiguate. Resolving every time is deliberate: a cached id would
        keep being used after a second Studio opened or the first restarted,
        which is exactly the ambiguity this refuses.

        A fresh proxy needs a moment after its handshake before its Studio
        uplink is usable; until then ``list_roblox_studios`` fails with
        "Unable to reach Roblox Studio". That specific transient symptom is
        retried until ``timeout`` seconds have elapsed. Every other error —
        including a genuinely empty instance list — still raises immediately.
        """
        if self._studio_id:
            return self._studio_id

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                studios = await self.list_studios()
            except MCPToolError as exc:
                if not _is_not_ready_error(exc) or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(max(0.0, min(interval, deadline - time.monotonic())))
                continue
            break

        if not studios:
            raise MCPToolError(
                "No Roblox Studio instances are connected. Open Studio and enable "
                "the MCP plugin, then retry."
            )
        if len(studios) > 1:
            raise MCPToolError(
                f"{len(studios)} Roblox Studio instances are connected, so no "
                f"studio_id can be inferred: "
                f"{[(s.get('name'), s.get('id')) for s in studios]!r}. "
                f"Pass studio_id= to connect() (or per call) to pick one."
            )
        only = studios[0]
        for key in ("id", "studio_id", "studioId"):
            if only.get(key):
                return str(only[key])
        raise MCPToolError(
            "Could not determine studio_id from list_roblox_studios result: "
            f"{only!r}"
        )

    # ------------------------------------------------------------------ #
    # Tool calling
    # ------------------------------------------------------------------ #
    async def call(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> CallToolResult:
        """Call a Studio MCP tool, injecting ``studio_id`` when required.

        ``studio_id`` is only added when the tool's input schema declares it and
        the caller did not already supply one.

        A pinned ``studio_id`` that the proxy can no longer reach is reported
        with what *is* currently connected, and is not silently swapped for a
        different Studio: a pin is the caller's explicit choice, so replacing
        it would reintroduce the guessing this layer exists to avoid. Unpin
        with ``set_studio_id(None)`` to fall back to inference.
        """
        args = dict(arguments or {})
        used_pinned = False

        if "studio_id" not in args:
            try:
                tool = await self.get_tool(name)
            except MCPToolError:
                tool = None
            if tool is None or tool.has_parameter("studio_id"):
                used_pinned = self._studio_id is not None
                args["studio_id"] = await self.resolve_studio_id()

        try:
            return await self._client.call_tool(name, args)
        except MCPToolError as exc:
            if not used_pinned or not _is_stale_studio_id_error(exc):
                raise
            raise MCPToolError(
                f"The pinned studio_id {args['studio_id']!r} is no longer "
                f"connected: {exc} "
                f"Studio instance ids change every time Studio restarts, so a "
                f"pin does not survive one. Re-pin with set_studio_id() using a "
                f"current id from list_studios(), or set_studio_id(None) to fall "
                f"back to inferring from whichever single Studio is open."
            ) from exc

    # ------------------------------------------------------------------ #
    # Typed convenience methods
    # ------------------------------------------------------------------ #
    async def execute_luau(
        self,
        code: str,
        datamodel_type: str = "Edit",
        **kwargs: Any,
    ) -> CallToolResult:
        """Run Luau code inside Studio and return the result."""
        return await self.call(
            "execute_luau",
            {"code": code, "datamodel_type": datamodel_type, **kwargs},
        )

    async def get_studio_state(self) -> CallToolResult:
        """Return Studio's play state and available datamodel types."""
        return await self.call("get_studio_state", {})

    async def start_play(self) -> CallToolResult:
        """Start play testing."""
        return await self.call("start_stop_play", {"is_start": True})

    async def stop_play(self) -> CallToolResult:
        """Stop play testing and return to edit mode."""
        return await self.call("start_stop_play", {"is_start": False})

    async def script_read(self, target_file: str, **kwargs: Any) -> CallToolResult:
        """Read a script from the DataModel (e.g. ``game.ServerScriptService.MyScript``)."""
        return await self.call("script_read", {"target_file": target_file, **kwargs})

    async def inspect_instance(self, path: str) -> CallToolResult:
        """Inspect an instance's properties, attributes and children."""
        return await self.call("inspect_instance", {"path": path})

    async def search_game_tree(self, **kwargs: Any) -> CallToolResult:
        """Explore the DataModel hierarchy."""
        return await self.call("search_game_tree", kwargs)

    async def screen_capture(self, capture_id: str = "ScreenCapture_1", **kwargs: Any) -> CallToolResult:
        """Capture the current Studio viewport."""
        return await self.call("screen_capture", {"capture_id": capture_id, **kwargs})


def default_command() -> str:
    """The command used to launch the Studio MCP proxy (platform-aware)."""
    command, _, _ = platform_defaults()
    return command


def default_shell() -> bool:
    """Whether the proxy launch needs shell mode (platform-aware)."""
    _, _, shell = platform_defaults()
    return shell


def default_args() -> List[str]:
    """The arguments used to launch the Studio MCP proxy (platform-aware).

    ``%LOCALAPPDATA%`` is intentionally left unexpanded: the proxy is launched in
    shell mode on Windows, so ``cmd.exe`` expands it itself. macOS takes no args.
    """
    _, args, _ = platform_defaults()
    return list(args)


# --------------------------------------------------------------------------- #
# Process-wide singleton connection
# --------------------------------------------------------------------------- #
_singleton: Optional["RobloxStudio"] = None
_singleton_lock: Optional["asyncio.Lock"] = None


def _get_lock() -> "asyncio.Lock":
    """Return the process-wide async lock, creating it lazily.

    Lazily created so importing this module outside a running event loop
    never binds an asyncio.Lock to the wrong loop.
    """
    global _singleton_lock
    if _singleton_lock is None:
        _singleton_lock = asyncio.Lock()
    return _singleton_lock


async def get_singleton(
	studio_id: Optional[str] = None,
	**kwargs: Any,
) -> "RobloxStudio":
    """Return a process-wide shared :class:`RobloxStudio` connection.

    The first call creates and connects a client; every subsequent call returns
    the same instance (reconnecting only if the underlying connection dropped).
    This gives you a single shared connection instead of spawning a fresh proxy
    per call. Close it with :func:`close_singleton`.

    Parameters
    ----------
    studio_id:
        Optional Studio instance to target (resolved lazily if omitted).
    **kwargs:
        Forwarded to :meth:`RobloxStudio.connect`.
    """
    global _singleton
    async with _get_lock():
        if _singleton is None or not _singleton.client.is_connected:
            _singleton = await RobloxStudio.connect(studio_id=studio_id, singleton=False, **kwargs)
            _singleton._is_singleton = True
        elif studio_id and _singleton.studio_id != studio_id:
            # Caller wants a different Studio target: re-point the shared
            # instance instead of spawning a second proxy process.
            _singleton.set_studio_id(studio_id)
        return _singleton


async def close_singleton() -> None:
    """Close and forget the shared connection created by :func:`get_singleton`."""
    global _singleton
    if _singleton is not None:
        await _singleton.close()
        _singleton = None
