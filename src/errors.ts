/** Exceptions raised by the `roblox-studio-mcp-node` package. */

/** Base class for all errors raised by this package. */
export class MCPError extends Error {
  constructor(message?: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "MCPError";
  }
}

/** Raised when the connection to an MCP server fails or drops. */
export class MCPConnectionError extends MCPError {
  constructor(message?: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "MCPConnectionError";
  }
}

/** Raised when the server sends something that violates the protocol. */
export class MCPProtocolError extends MCPError {
  constructor(message?: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "MCPProtocolError";
  }
}

/**
 * Raised when a tool call fails.
 *
 * This covers two cases: the JSON-RPC layer reporting an error for the
 * `tools/call` request, or the tool itself reporting `isError: true`.
 */
export class MCPToolError extends MCPError {
  constructor(message?: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "MCPToolError";
  }
}

export interface JSONRPCErrorDict {
  code?: number;
  message?: string;
  data?: unknown;
}

/**
 * A JSON-RPC 2.0 error object returned by the server.
 *
 * Attributes mirror the JSON-RPC spec: `code`, `message` and an optional
 * `data` payload.
 */
export class JSONRPCError extends MCPError {
  code: number;
  message: string;
  data: unknown;

  constructor(code: number, message: string, data: unknown = undefined) {
    super(`[${code}] ${message}`);
    this.name = "JSONRPCError";
    this.code = code;
    this.message = message;
    this.data = data;
  }

  static fromDict(error: JSONRPCErrorDict): JSONRPCError {
    return new JSONRPCError(
      error.code ?? -1,
      error.message ?? "Unknown JSON-RPC error",
      error.data,
    );
  }
}
