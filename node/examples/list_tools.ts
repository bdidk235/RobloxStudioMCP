/** List every tool exposed by the Roblox Studio MCP server. */

import { MCPClient, RobloxStudio } from "../src/index.js";

async function main(): Promise<void> {
  const studio = await RobloxStudio.connect();
  try {
    const serverInfo = (studio.client as MCPClient).serverInfo;
    console.log(`Server: ${String(serverInfo["name"] ?? "?")}`);

    const tools = await studio.listTools();
    for (const tool of tools) {
      const required = tool.required.join(", ") || "(none)";
      console.log(`  ${tool.name}  [required: ${required}]`);
    }
  } finally {
    await studio.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
