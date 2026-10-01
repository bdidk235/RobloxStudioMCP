"""Live integration tests against a real Roblox Studio with MCP enabled.

Skipped unless ``ROBLOX_STUDIO_MCP_INTEGRATION=1`` is set. Studio must be
open with a place loaded and the MCP server enabled (Assistant -> Manage
MCP Servers). In CI this is handled by the ``python-studio`` job — see
``.github/workflows/ci.yml`` — which installs Studio, waits for it with
``python -m examples.wait_for_studio``, then runs this suite.

The probe script uses a fixed DataModel path so reruns are idempotent
(``write_script`` just reports ``"unchanged"``).
"""

import os
import unittest

from roblox_studio_mcp import RobloxStudio
from roblox_studio_mcp.extended import write_script
from roblox_studio_mcp.extended.writer import _strip_line_prefixes

INTEGRATION = os.environ.get("ROBLOX_STUDIO_MCP_INTEGRATION") == "1"

PROBE_PATH = "game.ServerScriptService.MCPIntegrationProbe"
PROBE_SOURCE = "print('MCP integration probe')\n"


@unittest.skipUnless(
    INTEGRATION,
    "needs live Studio (ROBLOX_STUDIO_MCP_INTEGRATION=1)",
)
class TestStudioIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_execute_write_read(self):
        async with await RobloxStudio.connect(singleton=False) as studio:
            sid = await studio.resolve_studio_id()
            self.assertTrue(sid)

            names = [t.name for t in await studio.list_tools()]
            self.assertIn("execute_luau", names)

            result = await studio.execute_luau("return 6 * 7")
            self.assertEqual(result.text().strip(), "42")

            status = await write_script(
                studio, PROBE_PATH, PROBE_SOURCE, create_if_missing=True
            )
            self.assertIn(status, ("wrote", "created", "unchanged"))

            read_back = await studio.script_read(PROBE_PATH)
            self.assertEqual(_strip_line_prefixes(read_back.text()), PROBE_SOURCE)

            state = await studio.get_studio_state()
            self.assertTrue(state.text())


if __name__ == "__main__":
    unittest.main()
