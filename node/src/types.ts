/** Data types exchanged with an MCP server. */

export interface ToolDict {
  name?: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export type ContentBlock = string | { type?: string; text?: string; [key: string]: unknown };

export interface CallToolResultDict {
  content?: ContentBlock[];
  isError?: boolean;
}

/** A single tool advertised by the MCP server. */
export class Tool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  /**
   * Whether this tool only observes.
   *
   * Absent means "may mutate", so a new tool is safe by default rather than a way
   * to accidentally grant parallel execution. Set from `READ_ONLY_TOOLS` in
   * extendedServer.ts, mirroring the Python server - the parity test asserts the
   * two agree, because a client that grants parallel execution from this hint is
   * exactly why the hint has to match.
   */
  readOnly?: boolean;

  constructor(
    name: string,
    description = "",
    inputSchema: Record<string, unknown> = {},
  ) {
    this.name = name;
    this.description = description;
    this.inputSchema = inputSchema;
  }

  static fromDict(data: ToolDict): Tool {
    return new Tool(
      data.name ?? "",
      data.description ?? "",
      (data.inputSchema as Record<string, unknown>) ?? {},
    );
  }

  /** The tool's input properties (name -> JSON Schema). */
  get properties(): Record<string, unknown> {
    const props = (this.inputSchema as { properties?: unknown }).properties;
    return (props as Record<string, unknown>) ?? {};
  }

  /** The tool's required input parameter names. */
  get required(): string[] {
    const req = (this.inputSchema as { required?: unknown }).required;
    return Array.isArray(req) ? (req as string[]) : [];
  }

  /** Whether the tool's input schema declares `name`. */
  hasParameter(name: string): boolean {
    return name in this.properties;
  }

  toString(): string {
    return `Tool(name=${JSON.stringify(this.name)})`;
  }
}

/**
 * Return the first balanced `{...}` or `[...]` prefix of `text`, if any.
 *
 * Respects JSON strings and escapes so braces inside strings don't break
 * the depth count. Returns `null` when brackets never balance.
 */
export function extractBalanced(text: string): string | null {
  if (!text || (text[0] !== "{" && text[0] !== "[")) {
    return null;
  }
  const pairs: Record<string, string> = { "{": "}", "[": "]" };
  const closing = pairs[text[0]];
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === "{" || ch === "[") {
      // Count all bracket types to stay balanced for nested structures
      // like {"a": [1]}.
      depth += 1;
    } else if (ch === "}" || ch === "]") {
      depth -= 1;
      if (depth === 0) {
        // Ensure the outer type closes correctly.
        if (ch === closing) {
          return text.slice(0, i + 1);
        }
        return null;
      }
    }
  }
  return null;
}

/** The result of a `tools/call` request. */
export class CallToolResult {
  content: ContentBlock[];
  isError: boolean;

  constructor(content: ContentBlock[] = [], isError = false) {
    this.content = content;
    this.isError = isError;
  }

  static fromDict(data: CallToolResultDict): CallToolResult {
    return new CallToolResult(data.content ?? [], Boolean(data.isError ?? false));
  }

  /** Concatenate all `text` content blocks into a single string. */
  text(): string {
    const parts: string[] = [];
    for (const block of this.content) {
      if (typeof block === "string") {
        parts.push(block);
      } else if (block !== null && typeof block === "object" && block.type === "text") {
        parts.push(String(block.text ?? ""));
      }
    }
    return parts.join("\n");
  }

  /**
   * Best-effort parse of the text content as JSON.
   *
   * Handles plain JSON, JSON wrapped in Markdown code fences, and JSON
   * embedded in surrounding prose. Returns `null` when nothing parses.
   */
  json(): unknown {
    let text = this.text().trim();
    if (!text) {
      return null;
    }

    // Strip Markdown code fences if present (```json ... ``` or ``` ... ```).
    if (text.startsWith("```")) {
      let lines = text.split("\n");
      // Drop opening fence (``` or ```json).
      lines = lines.slice(1);
      // Drop trailing fence if present.
      if (lines.length > 0 && lines[lines.length - 1].trim().startsWith("```")) {
        lines = lines.slice(0, -1);
      }
      text = lines.join("\n").trim();
    }

    try {
      return JSON.parse(text);
    } catch {
      // fall through to balanced extraction
    }

    // Fall back to extracting the first balanced object/array, trying
    // each candidate start position in order (handles nested JSON and
    // multiple candidates in prose).
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch !== "{" && ch !== "[") {
        continue;
      }
      const candidate = extractBalanced(text.slice(i));
      if (candidate === null) {
        continue;
      }
      try {
        return JSON.parse(candidate);
      } catch {
        continue;
      }
    }
    return null;
  }

  toString(): string {
    let preview = this.text();
    if (preview.length > 80) {
      preview = preview.slice(0, 77) + "...";
    }
    return `CallToolResult(isError=${JSON.stringify(this.isError)}, text=${JSON.stringify(preview)})`;
  }
}
