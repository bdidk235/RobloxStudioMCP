"use strict";
/* All page content as data. app.js renders it with the components.
   Keep HTML here static and trusted (no user input ever flows through). */

window.DOC = {
  sections: [
    {
      id: "chain",
      title: "1. The connection chain",
      blocks: [
        { type: "diagram" },
        {
          type: "p",
          html: "Everything starts with <code>mcp.bat</code> (<code>%LOCALAPPDATA%\\Roblox\\mcp.bat</code>), " +
            "which launches <code>StudioMCP.exe</code> — Roblox's own MCP proxy (v1.0.0). " +
            "On macOS the same proxy ships inside the app bundle and runs directly " +
            "(<code>/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP</code>, no shell wrapper). " +
            "Clients speak newline-delimited JSON-RPC 2.0 over its stdin/stdout: one JSON object per line, " +
            "answered with <code>initialize</code> handshake, <code>tools/list</code>, and <code>tools/call</code>. " +
            "Server-to-client requests are answered <code>Method not found</code> so nothing ever blocks.",
        },
        {
          type: "p",
          html: "The proxy does <em>not</em> talk to Studio over stdio. It joins a WebSocket mesh at " +
            "<code>ws://127.0.0.1:13469/proxy</code>: the <strong>first</strong> proxy process hosts port 13469 " +
            "(later ones log <code>Port already in use, waiting for signal to retry</code> and join as clients). " +
            "Studio itself attaches to that mesh — but only with a place open <em>and</em> its MCP server enabled. " +
            "No place, no MCP toggle, no Studio in the mesh: that is exactly what the famous " +
            "<code>Unable to reach Roblox Studio</code> error means.",
        },
        {
          type: "callout",
          kind: "story",
          html: "Traced live with <code>StudioMCP.exe --verbose</code>: a probe joined the mesh and was handed " +
            "<strong>27 tools</strong> relayed from the hub — the moment the proxy side of a confusing outage was " +
            "proven healthy and the hunt moved (correctly) to Studio's attach toggle.",
        },
      ],
    },
    {
      id: "servers",
      title: "2. The two servers in this repo",
      blocks: [
        {
          type: "p",
          html: "<code>node node/dist/server.js</code> (from the repo root) is a transparent proxy: same tools, same raw responses as " +
            "StudioMCP, one upstream process reused for the server's lifetime. " +
            "<code>node node/dist/extendedServer.js</code> layers 8 convenience tools on top and is what opencode " +
            "actually runs (<code>Roblox_Studio</code> in <code>opencode.jsonc</code>). Measured " +
            "<code>tools/list</code> through it: <strong>36 tools</strong> — 28 relayed, 8 extended.",
        },
        {
          type: "code",
          text: '{\n  "Roblox_Studio": {\n    "type": "local",\n    "command": ["node", "node/dist/extendedServer.js"],\n    "enabled": true\n  }\n}',
        },
      ],
    },
    {
      id: "startup",
      title: "3. Startup timing: handshake ≠ ready",
      blocks: [
        {
          type: "p",
          html: "Spawning the proxy and finishing the MCP handshake takes ~80–130&nbsp;ms. But a fresh proxy's " +
            "Studio uplink needs another beat: calls landing in the first milliseconds fail with " +
            "<code>Unable to reach Roblox Studio</code> even though Studio is attached. Measured spawn-to-first-live-answer: " +
            "<strong>~204 ms, identical in Python and Node</strong> — it is proxy-side, not language.",
        },
        {
          type: "p",
          html: "Both clients ride through this: <code>resolve_studio_id()</code> / <code>resolveStudioId()</code> " +
            "retry <em>only</em> that transient symptom (up to 10 s, every 200 ms). Any other error — including a " +
            "genuinely empty Studio list — still raises immediately. So the first tool call through a new " +
            "connection, singleton included, just works.",
        },
        {
          type: "p",
          html: "An <em>implicit</em> id is accepted only when exactly one Studio is connected. With two or more, " +
            "<code>resolve_studio_id()</code> raises and names every candidate instead of taking the first entry. " +
            "List order comes from the proxy mesh and carries no intent, so choosing by position silently routes " +
            "work to the wrong Studio — and the reply is well-formed, so the mistake is invisible. Resolution is " +
            "also re-run per call rather than cached, so a second Studio opening or the first restarting is noticed " +
            "immediately.",
        },
        {
          type: "p",
          html: "Because <code>studio_id</code> is minted by the proxy and changes on every restart, it is a " +
            "transport token rather than an identity. <code>game.UniqueId</code> is unreadable here (it needs the " +
            "<code>RobloxScript</code> capability), but <code>game:GetDebugId()</code> is not — so " +
            "<code>extended_studio_identity</code> and <code>extended_list_studios</code> pair the two and keep a " +
            "registry in this machine's state directory. That registry is host-side only: nothing is written into " +
            "the DataModel, so an entry cannot reach the place file, a published place, or a team create.",
        },
        {
          type: "callout",
          kind: "warn",
          html: "Found with a raw-bytes pacing probe: burst (call instantly) failed 100% of the time, " +
            "1-second-paced succeeded 100%. If you write your own client, do not treat the first " +
            "<code>Unable to reach</code> as fatal — poll briefly first.",
        },
      ],
    },
    {
      id: "tools",
      title: "4. Full tool catalog — 28 relayed, 8 extended",
      blocks: [
        {
          type: "p",
          html: "Every card shows required inputs, optional inputs, and what comes back. Nearly every raw tool " +
            "also takes <code>studio_id</code> (which attached Studio to target — see " +
            "<code>list_roblox_studios</code>); it is omitted per-card to reduce noise. " +
            "Rule of thumb: full-file writes → extended write; targeted string edits → extended update; " +
            "either beats raw <code>multi_edit</code>, which fails the whole call on any invalid edit.",
        },
        { type: "tools" },
      ],
    },
    {
      id: "chunked",
      title: "5. Big payloads: chunked writes",
      blocks: [
        {
          type: "p",
          html: "Studio rejects direct <code>Script.Source</code> assignment over ~200K. " +
            "<code>write_script</code> / <code>writeScript</code> therefore split large bodies " +
            "into 200K slices embedded as raw Lua long-bracket strings, then loop " +
            "<code>ScriptEditorService:UpdateSourceAsync(target, function(old) return old .. slice end)</code> " +
            "inside Studio. Proven live: a <strong>12.2 MB module rewritten byte-exact</strong> (12,220,210 chars).",
        },
        {
          type: "callout",
          kind: "story",
          html: "That 12 MB write caught a real bug: Lua <strong>skips a leading newline</strong> in long-bracket " +
            "literals, so every 200K slice starting with <code>\\n</code> silently lost one char (exactly 2 in " +
            "12.2 MB — at chars 4,000,000 and 10,200,000). The fix prepends a guard newline Lua consumes instead. " +
            "Same session also fixed the loop capturing shared <code>i</code> instead of <code>local idx</code> " +
            "inside the async callbacks.",
        },
        {
          type: "callout",
          kind: "warn",
          html: "<code>execute_luau</code> has no such 200K ceiling — a 921 KB single-shot execute ran in ~179 ms. " +
            "But its parser rejects <code>declare</code> blocks: <code>.d.luau</code> definition files belong in " +
            "the typechecker, not the DataModel.",
        },
      ],
    },
    {
      id: "perf",
      title: "6. Measured performance",
      blocks: [
        {
          type: "p",
          html: "Python 3.12 vs Node 26, same Studio, same targets, 20 iterations per op, sequential runs. " +
            "Verdict across three rounds: <strong>no meaningful difference</strong> — both clients are thin " +
            "pipes over the same proxy; the Studio roundtrip dominates.",
        },
        { type: "perf" },
      ],
    },
    {
      id: "repo",
      title: "7. Repo layout & workflow",
      blocks: [
        {
          type: "code",
          text: "node/\n  src/              public API (MCPClient, RobloxStudio, …), servers, extended/\n  tests/            vitest suites (core, improvements, live-Studio integration)\n  examples/         runnable TS examples (incl. wait_for_studio for CI)\npython/               original Python client (same API, snake_case)\n  src/roblox_studio_mcp/\n  tests/            unittest suites (test_core, test_improvements, live-Studio integration)\n  examples/         runnable Python examples\ndocs/               this site (opens from disk)\n",
        },
        {
          type: "list",
          items: [
            "<code>pnpm --dir node install</code> (if pnpm demands a TTY purge: <code>CI=true pnpm --dir node install</code>)",
            "<code>pnpm --dir node typecheck</code> — strict tsc over src, tests, examples",
            "<code>pnpm --dir node test</code> — vitest, no Studio needed (fakes throughout)",
            "<code>pnpm --dir node build</code> — wipes and rebuilds <code>node/dist/</code> to mirror package entry points",
          ],
        },
        {
          type: "callout",
          kind: "warn",
          html: "PowerShell quirk that bit twice: <code>echo … | exe</code> keeps the child's stdin open, so " +
            "stdio servers never see EOF and linger (with their proxies). Drive subprocess pipes from a script " +
            "that closes stdin — or expect orphans and clean them up.",
        },
      ],
    },
  ],
  perf: [
    ["cold connect (×5)", "100.6 ms", "127.8 ms", "Python spawns ~25 ms quicker"],
    ["time-to-ready", "203.9 ms", "205.0 ms", "identical — proxy-side"],
    ["resolve (cached)", "1.0 ms", "1.3 ms", "noise"],
    ["list_tools", "2.1 ms", "1.9 ms", "client floor, identical"],
    ["execute_luau", "37.2 ms", "46.1 ms", "overlapping (sd ~20)"],
    ["script_read 85KB", "40.3 ms", "63.3 ms", "overlapping (sd ~25)"],
    ["get_studio_state", "25.4 ms", "25.8 ms", "identical"],
    ["get_console_output", "24.7 ms", "36.4 ms", "overlapping"],
  ],
};
