/**
 * roblox-studio-mcp: a dependency-free Node.js client for MCP servers.
 *
 * Built with the Roblox Studio MCP in mind, but {@link MCPClient} is a
 * generic stdio MCP client that works with any server.
 */

export { VERSION } from "./version.js";
export { MCPClient, DEFAULT_PROTOCOL_VERSION, DEFAULT_TIMEOUT, expandEnvVars } from "./client.js";
export type { MCPClientOptions } from "./client.js";
export {
  MCPError,
  MCPConnectionError,
  MCPProtocolError,
  MCPToolError,
  JSONRPCError,
} from "./errors.js";
export type { JSONRPCErrorDict } from "./errors.js";
export {
  RobloxStudio,
  getSingleton,
  closeSingleton,
  defaultCommand,
  defaultArgs,
  defaultShell,
  platformDefaults,
  MACOS_COMMAND,
  WINDOWS_COMMAND,
  WINDOWS_ARGS,
} from "./roblox.js";
export type {
  RobloxStudioConnectOptions,
  SingletonOptions,
  StudioClientLike,
  PlatformDefaults,
  ResolveOptions,
} from "./roblox.js";
export { CallToolResult, Tool, extractBalanced } from "./types.js";
export type { ToolDict, CallToolResultDict, ContentBlock } from "./types.js";
