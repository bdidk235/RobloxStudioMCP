"""A fake Studio MCP proxy, for the doctor tests. Not part of the package.

Speaks just enough JSON-RPC over stdio to answer what the doctor asks:
``initialize``, ``tools/list`` and ``list_roblox_studios``. Launched as a
subprocess by ``python/tests/test_doctor.py`` with

    python fake_proxy.py --mode ok|drift|no-studio|mute

so every arm of the doctor's story is exercised without a live Studio:

``ok``
    The happy path: the tools/list payload is rebuilt from
    ``contract/upstream_tools.json`` - same names, same description lengths,
    same property shapes - so the doctor's snapshot comparison finds zero
    drift, and one Studio instance is reported.
``drift``
    The upstream surface moved: one pinned tool is missing (a failure), one
    brand-new tool appears (a warning) and one tool's required set changed
    (a failure).
``no-studio``
    A healthy proxy that cannot see Studio - the MCP-toggle-off symptom.
``mute``
    Exits without speaking: the handshake-failure arm.

The reconstruction from the snapshot is deliberate. A fake that invented its
own tool shapes would make the doctor's step-4 comparison warn about every
description length, and the success arm would prove nothing except that
warnings print.
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SNAPSHOT_PATH = os.path.join(_ROOT, "contract", "upstream_tools.json")

SERVER_INFO = {"name": "FakeStudioMCP", "version": "0.0.0-test"}
STUDIOS = [{"id": "studio-fake-1", "name": "Fake Place"}]
NEW_TOOL = {
    "name": "brand_new_upstream_tool",
    "description": "A tool Studio shipped that the snapshot does not pin.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
}


def _load_snapshot_tools():
    with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    tools = []
    for entry in snapshot.get("tools") or []:
        properties = {}
        for name, spec in (entry.get("properties") or {}).items():
            shape = {"type": spec.get("type")}
            if "items_type" in spec:
                shape["items"] = {"type": spec["items_type"]}
            if "enum" in spec:
                shape["enum"] = list(spec["enum"])
            if spec.get("has_default"):
                shape["default"] = None
            properties[name] = shape
        tools.append(
            {
                "name": entry["name"],
                "description": "x" * int(entry.get("description_chars") or 0),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(entry.get("required") or []),
                },
            }
        )
        if entry.get("read_only"):
            tools[-1]["annotations"] = {"readOnlyHint": True}
    return tools


def _drifted_tools(tools):
    """One drop (fails), one arrival (warns), one required-set change (fails)."""
    out = [t for t in tools if t["name"] != tools[0]["name"]]
    out = [dict(NEW_TOOL)] + out
    for tool in out:
        if tool["name"] == tools[1]["name"]:
            required = list(tool["inputSchema"].get("required") or [])
            tool["inputSchema"]["required"] = required + ["extra_required_arg"]
    return out


def _send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _result(ident, result):
    _send({"jsonrpc": "2.0", "id": ident, "result": result})


def _tool_error(ident, text):
    _send(
        {
            "jsonrpc": "2.0",
            "id": ident,
            "error": {"code": -32000, "message": text},
        }
    )


def _tool_content_error(ident, name, text):
    """An ``isError`` tool result, the shape RobloxStudio surfaces as a raise."""
    _result(
        ident,
        {
            "content": [{"type": "text", "text": text}],
            "isError": True,
            "is_error": True,
        },
    )


def serve(mode):
    tools = _load_snapshot_tools()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        ident = message.get("id")
        if method == "initialize":
            _result(
                ident,
                {
                    "protocolVersion": (message.get("params") or {}).get(
                        "protocolVersion", "2024-11-05"
                    ),
                    "capabilities": {},
                    "serverInfo": SERVER_INFO,
                },
            )
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue  # a notification: no response
        elif method == "ping":
            _result(ident, {})
        elif method == "tools/list":
            listed = _drifted_tools(tools) if mode == "drift" else tools
            _result(ident, {"tools": listed})
        elif method == "tools/call":
            name = (message.get("params") or {}).get("name")
            if name == "list_roblox_studios":
                if mode == "no-studio":
                    _tool_content_error(
                        ident, name, "Unable to reach Roblox Studio"
                    )
                else:
                    _result(
                        ident,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"studios": STUDIOS}),
                                }
                            ],
                            "isError": False,
                            "is_error": False,
                        },
                    )
            elif name == "get_studio_state":
                _result(
                    ident,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"play_state": "Edit"}),
                            }
                        ],
                        "isError": False,
                        "is_error": False,
                    },
                )
            else:
                _tool_error(ident, "unknown tool %r" % name)
        elif ident is not None:
            _tool_error(ident, "method not found: %r" % method)
    return 0


def main(argv):
    mode = "ok"
    if "--mode" in argv:
        mode = argv[argv.index("--mode") + 1]
    if mode == "mute":
        return 0
    return serve(mode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
