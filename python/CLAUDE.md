# Claude.md — Roblox Studio MCP Project

## MCP Server

Uses the **extended** Studio MCP server (`roblox_studio_mcp.extended_server`) which adds 8
convenience tools on top of the raw Studio MCP tools:

- `extended_write_like_multi_edit` — full-body script replacement with status return (`created` / `wrote` / `unchanged`)
- `extended_update_like_multi_edit` — batch edits with graceful skipping (`skip_missing`, `skip_no_ops`) and structured `UpdateResult`
- `extended_script_search_and_read` — search for scripts + batch-read their source
- `extended_insert_asset_from_file` — insert local files (images via store_image, scripts via write_like_multi_edit)
- `extended_watch_output` — live-tail Studio console output (new lines only)
- `extended_create_module_with_deps` — create ModuleScript + optionally add require() to target
- `extended_run_tests` — play test + capture console output as test summary
- `extended_execute_luau_from_file` — execute Luau source read from a local file

### Registered as `Roblox_Studio` MCP client in Claude Code settings.

## MCP Tool Inventory (current session)

### Scripts & Search
- `mcp__Roblox_Studio__script_read`
- `mcp__Roblox_Studio__script_search`
- `mcp__Roblox_Studio__script_grep`
- `mcp__Roblox_Studio__search_game_tree`
- `mcp__Roblox_Studio__inspect_instance`
- `mcp__Roblox_Studio__extended_script_search_and_read`

### Editing
- `mcp__Roblox_Studio__multi_edit` (raw string replacement)
- `mcp__Roblox_Studio__extended_write_like_multi_edit` (full-body replace)
- `mcp__Roblox_Studio__extended_update_like_multi_edit` (batch, graceful skip)
- `mcp__Roblox_Studio__extended_insert_asset_from_file` (local file → asset insert)

### Extended Conveniences
- `mcp__Roblox_Studio__extended_watch_output` (live-tail console output)
- `mcp__Roblox_Studio__extended_create_module_with_deps` (create ModuleScript + require)
- `mcp__Roblox_Studio__extended_run_tests` (play testing + console summary)

### Code Execution
- `mcp__Roblox_Studio__execute_luau`
- `mcp__Roblox_Studio__extended_execute_luau_from_file` (local file → execute)

### Assets
- `mcp__Roblox_Studio__search_asset`
- `mcp__Roblox_Studio__insert_asset`
- `mcp__Roblox_Studio__store_image`

### Studio Control
- `mcp__Roblox_Studio__list_roblox_studios`
- `mcp__Roblox_Studio__get_studio_state`
- `mcp__Roblox_Studio__start_stop_play`
- `mcp__Roblox_Studio__get_console_output`

### Generation
- `mcp__Roblox_Studio__generate_procedural_model`
- `mcp__Roblox_Studio__generate_texture`
- `mcp__Roblox_Studio__run_as_job`
- `mcp__Roblox_Studio__skill`

### Interaction (Client dataModel only)
- `mcp__Roblox_Studio__character_navigation`
- `mcp__Roblox_Studio__user_keyboard_input`
- `mcp__Roblox_Studio__user_mouse_input`
- `mcp__Roblox_Studio__screen_capture`

### Permissions
Per user settings.json, the following are **denied**:
- `Bash`
- `mcp__Roblox_Studio__generate_mesh`
- `mcp__Roblox_Studio__segment_mesh`
- `mcp__Roblox_Studio__generate_material`
- `mcp__Roblox_Studio__subagent`
- `mcp__Roblox_Studio__upload_image`

## Programmatic Use

All extended tools can also be used directly from Python:

```python
import asyncio
from roblox_studio_mcp.extended import RobloxStudio, write_like_multi_edit

async def main():
    async with await RobloxStudio.connect() as studio:
        status = await write_like_multi_edit(
            studio,
            "game.ServerScriptService.MyScript",
            "print('hello')",
            create_if_missing=True,
        )
        # status is "created", "wrote", or "unchanged"

asyncio.run(main())
```

## Project Structure

```
src/roblox_studio_mcp/
├── __init__.py
├── client.py        ← generic MCP JSON-RPC client
├── roblox.py        ← RobloxStudio convenience wrapper
├── server.py        ← stdio MCP server (base, passes through to Studio MCP)
├── extended.py      ← RobloxStudio re-export for subpackage imports
├── extended_server.py ← stdio MCP server with added extended tools
├── extended/        ← extended tools (write/update + 5 extensions)
│   ├── __init__.py
│   ├── writer.py    ← write_like_multi_edit + chunked _chunked_write
│   ├── updater.py   ← update_like_multi_edit + UpdateResult
│   └── extensions.py ← script_search_and_read, insert_asset_from_file,
│                       watch_output, create_module_with_deps, run_tests,
│                       execute_luau_from_file
└── ... (types, errors, _version, etc.)
```

### Chunked Write (>200K content)

`_chunked_write` uses multiple `execute_luau` calls to pass more than 200K lines
as Lua long-bracket strings (`[==========[..]==========]`),
iterates with `UpdateSourceAsync(target, function(old) return old .. slice end)`.
Studio handles the >200K accumulation internally, bypassing the `Script.Source` limit.
