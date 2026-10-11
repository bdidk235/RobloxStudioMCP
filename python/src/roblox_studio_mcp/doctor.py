"""``python -m roblox_studio_mcp.doctor`` - can this machine reach Studio?

A diagnostic that answers the first-ten-minutes questions in order, and when
one fails, says what to do about it rather than repeating the traceback:

1. **Launch** the Studio MCP proxy and complete the MCP handshake.
2. **tools/list** the proxy exposes, split into the two trust labels -
   ``proxied_upstream`` (Roblox Studio's, graded against
   ``contract/upstream_tools.json``) and ``extended`` (this repo's, checked
   against ``contract/tools.json``).
3. **Studio reachability**: how many instances the proxy reports, by name and
   ``studio_id``.
4. Drift against the pinned upstream snapshot, graded: a rename or drop fails,
   an additive tool or reworded description warns.

Every failure names the fix, because the three things that actually go wrong
have three different owners:

* the proxy is not on PATH (Studio not installed / not repaired),
* Studio is closed or has no place open,
* the MCP plugin toggle inside Studio is off.

Launch overrides, for testing and for non-default installs:

* ``ROBLOX_STUDIO_MCP_PROXY_COMMAND`` - executable to launch instead of the
  platform default.
* ``ROBLOX_STUDIO_MCP_PROXY_ARGS`` - JSON array of arguments (a plain string is
  split on whitespace).
* ``ROBLOX_STUDIO_MCP_PROXY_SHELL`` - set to ``1`` to launch through a shell.

Exit code is 0 when nothing failed, 1 otherwise. Warnings do not fail: this is
a diagnostic, and flapping would train people to ignore it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .client import MCPClient
from .errors import MCPConnectionError, MCPError
from .roblox import RobloxStudio, default_args, default_command, default_shell
from .upstream import (
    TRUST_UPSTREAM,
    UpstreamReport,
    classify_names,
    compare_snapshot,
    load_snapshot,
    snapshot_names,
)

#: How long to wait on any single round trip before calling the proxy wedged.
DEFAULT_TIMEOUT = 30.0

#: The proxy's own symptom for "Studio is not answering me", quoted by
#: `roblox.py`'s retry loop for the same reason: it is the transient
#: uplink-warming case, distinct from a healthy proxy reporting zero Studios.
_NOT_REACHED = "unable to reach"

_FIX_PROXY_LAUNCH = (
    "FIX    the Studio MCP proxy did not launch. Roblox Studio installs it at\n"
    "       %LOCALAPPDATA%\\Roblox\\mcp.bat (Windows) or inside the Studio.app\n"
    "       bundle (macOS). Repair or reinstall Roblox Studio, or point\n"
    "       ROBLOX_STUDIO_MCP_PROXY_COMMAND at a working StudioMCP launcher."
)
_FIX_PROXY_HANDSHAKE = (
    "FIX    the proxy launched but the MCP handshake failed, so it is not\n"
    "       usable as an MCP server. Confirm the command really starts\n"
    "       StudioMCP (not a shell wrapper that returns immediately), and\n"
    "       that nothing else is holding its stdio pipes."
)
_FIX_STUDIO = (
    "FIX    Roblox Studio is not reachable. Open Roblox Studio with a place\n"
    "       loaded, and enable the MCP plugin (Studio's MCP server toggle -\n"
    "       the plugin must be on for the proxy to see an instance). With\n"
    "       Studio already open, toggling the plugin off and on re-registers\n"
    "       it; then re-run this doctor."
)
_FIX_UPSTREAM = (
    "FIX    the live upstream surface does not match the pinned snapshot. A\n"
    "       drop or rename upstream breaks callers at runtime. If the change\n"
    "       is real, refresh: python contract/build_upstream.py --live"
)


def _resolve_launch() -> Tuple[str, List[str], bool]:
    """The proxy launch settings, honouring the environment overrides.

    An override turns shell mode **off** unless ``ROBLOX_STUDIO_MCP_PROXY_SHELL``
    says otherwise: the platform default needs a shell only because it is a
    ``cmd.exe /c "..."`` form, and a pointed-at executable rarely does.
    """
    command = os.environ.get("ROBLOX_STUDIO_MCP_PROXY_COMMAND") or default_command()
    args_override = os.environ.get("ROBLOX_STUDIO_MCP_PROXY_ARGS")
    if args_override is not None:
        try:
            parsed = json.loads(args_override)
        except json.JSONDecodeError:
            parsed = args_override.split()
        args = [str(a) for a in parsed] if isinstance(parsed, list) else str(parsed).split()
        overridden = True
    else:
        args = default_args()
        overridden = os.environ.get("ROBLOX_STUDIO_MCP_PROXY_COMMAND") is not None
    if overridden:
        shell = os.environ.get("ROBLOX_STUDIO_MCP_PROXY_SHELL") == "1"
    else:
        shell = default_shell()
    return command, args, shell


def _find_upward(filename: str) -> Optional[str]:
    """Find ``filename`` in this directory or an ancestor, if present."""
    current = os.getcwd()
    while True:
        candidate = os.path.join(current, filename)
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _snapshot_path(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if os.path.exists(explicit) else None
    env = os.environ.get("ROBLOX_STUDIO_MCP_UPSTREAM_SNAPSHOT")
    if env:
        return env if os.path.exists(env) else None
    return _find_upward(os.path.join("contract", "upstream_tools.json"))


class _Report:
    """Prints as it goes and remembers whether anything failed."""

    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def line(self, text: str = "") -> None:
        print(text)

    def ok(self, text: str) -> None:
        print("  OK    %s" % text)

    def note(self, text: str) -> None:
        print("  ..    %s" % text)

    def warn(self, text: str) -> None:
        self.warnings += 1
        print("  WARN  %s" % text)

    def fail(self, text: str, fix: Optional[str] = None) -> None:
        self.failures += 1
        print("  FAIL  %s" % text)
        if fix:
            print("  %s" % fix)


async def _check(report: _Report, timeout: float, snapshot_path: Optional[str]) -> int:
    command, args, shell = _resolve_launch()
    report.line("Roblox Studio MCP doctor")
    report.line("  platform: %s" % sys.platform)
    report.line(
        "  proxy launch: %s%s"
        % (command, (" " + " ".join(args)) if args else " (shell)" if shell else "")
    )

    # ---------------------------------------------------------------- step 1
    report.line()
    report.line("1. Launch the proxy and complete the MCP handshake")
    client = MCPClient(command, args, shell=shell, timeout=timeout)
    try:
        await client.connect()
    except MCPConnectionError as exc:
        message = str(exc)
        if "not found" in message.lower():
            report.fail(message, _FIX_PROXY_LAUNCH)
        else:
            report.fail(message, _FIX_PROXY_HANDSHAKE)
        return report.failures
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not traceback
        report.fail("%s: %s" % (type(exc).__name__, exc), _FIX_PROXY_LAUNCH)
        return report.failures
    finally:
        if not client.is_connected:
            await client.close()

    try:
        server = client.server_info or {}
        report.ok(
            "proxy %s %s (protocol %s)"
            % (
                server.get("name", "?"),
                server.get("version", "?"),
                client.protocol_version,
            )
        )

        # ------------------------------------------------------------ step 2
        report.line()
        report.line("2. tools/list")
        try:
            tools = await client.list_tools()
        except Exception as exc:  # noqa: BLE001 - classified, not raised
            report.fail("tools/list failed: %s" % exc, _FIX_PROXY_HANDSHAKE)
            return report.failures
        from .extended_server import _EXTENDED_TOOLS

        split = classify_names((t.name for t in tools), (t.name for t in _EXTENDED_TOOLS))
        report.ok(
            "%d tools: %d %s, %d extended"
            % (len(tools), len(split["upstream"]), TRUST_UPSTREAM, len(split["extended"]))
        )
        if split["extended"]:
            # The raw proxy should not serve the extended tools; if it does, the
            # client is pointed at this package's own server, not Studio's.
            report.note(
                "the extended tools are this package's - if you expected the raw "
                "proxy, the launch command points at an extended server"
            )
        _check_extended_contract(report)

        # ------------------------------------------------------------ step 3
        report.line()
        report.line("3. Studio reachability")
        studio = RobloxStudio(client=client, studio_id=None)
        try:
            studios = await studio.list_studios()
        except Exception as exc:  # noqa: BLE001 - quoted, with the fix named
            report.fail(
                "list_roblox_studios failed: %s" % exc,
                _FIX_STUDIO,
            )
            return report.failures
        if not studios:
            report.fail(
                "the proxy reports no Studio instances", _FIX_STUDIO
            )
            return report.failures
        report.ok("%d Studio instance(s) connected" % len(studios))
        for row in studios:
            name = row.get("name") or row.get("reported_name") or "?"
            sid = row.get("id") or row.get("studio_id") or "?"
            report.line("        %r (studio_id %s)" % (name, sid))
        if _NOT_REACHED in json.dumps(studios).lower():
            report.warn(
                "the instance list mentions %r - the uplink may still be warming; "
                "re-run if a later step fails" % _NOT_REACHED
            )

        # ------------------------------------------------------------ step 4
        report.line()
        report.line("4. Upstream snapshot (%s)" % TRUST_UPSTREAM)
        _check_upstream(report, tools, snapshot_path)
    finally:
        await client.close()

    return report.failures


def _check_extended_contract(report: _Report) -> None:
    """The extended half of the surface: this repo's, pinned by tools.json."""
    from .extended_server import _EXTENDED_TOOLS

    path = _find_upward(os.path.join("contract", "tools.json"))
    if path is None:
        report.note(
            "extended surface: %d tools in this package; contract/tools.json "
            "not found (a checkout-only check), so not verified" % len(_EXTENDED_TOOLS)
        )
        return
    with open(path, encoding="utf-8") as handle:
        contract = json.load(handle)
    live = {t.name for t in _EXTENDED_TOOLS}
    pinned = {str(t["name"]) for t in contract.get("tools") or []}
    if live == pinned:
        report.ok(
            "extended surface: %d/%d tools match contract/tools.json"
            % (len(live & pinned), len(pinned))
        )
        return
    report.warn(
        "extended surface drifted from contract/tools.json: only in the "
        "package %s, only in the contract %s. Regenerate with `python "
        "contract/build_contract.py`."
        % (sorted(live - pinned), sorted(pinned - live))
    )


def _check_upstream(
    report: _Report, live_tools: Sequence[Any], snapshot_path: Optional[str]
) -> None:
    if snapshot_path is None:
        report.note(
            "no upstream snapshot found (looked for contract/upstream_tools.json "
            "upward from the working directory); skipped"
        )
        return
    try:
        snapshot = load_snapshot(snapshot_path)
    except (OSError, json.JSONDecodeError) as exc:
        report.warn("could not read %s: %s" % (snapshot_path, exc))
        return
    verdict: UpstreamReport = compare_snapshot(
        snapshot, [t.to_dict() for t in live_tools]
    )
    captured = snapshot.get("captured") or "unknown date"
    if verdict.ok:
        report.ok(
            "%d tools, no drift against the snapshot captured %s"
            % (verdict.checked, captured)
        )
        return
    for finding in verdict.failures + verdict.warnings:
        text = "%s %s: %s" % (finding.kind, finding.name, finding.detail)
        if finding.level == "FAIL":
            report.fail(text, _FIX_UPSTREAM)
        else:
            report.warn(text)
    report.note(
        "snapshot %s captured %s pins %s"
        % (snapshot_path, captured, ", ".join(snapshot_names(snapshot)))
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m roblox_studio_mcp.doctor",
        description="Check that this machine can reach Roblox Studio through "
        "the Studio MCP proxy.",
    )
    parser.add_argument(
        "--snapshot",
        default=None,
        help="path to contract/upstream_tools.json "
        "(default: found upward from the working directory)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="per-request timeout in seconds (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    report = _Report()
    snapshot_path = _snapshot_path(args.snapshot)
    failures = asyncio.run(_check(report, args.timeout, snapshot_path))

    report.line()
    if failures:
        print("doctor: FAIL (%d problem(s), %d warning(s))" % (failures, report.warnings))
        return 1
    print(
        "doctor: PASS%s" % ("" if not report.warnings else " (%d warning(s))" % report.warnings)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
