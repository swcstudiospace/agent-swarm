/**
 * Minimal structural types for the omp extension API surface this package uses (D-02).
 * Mirrors @oh-my-pi/pi-coding-agent 18.3.1 (identical in 18.0.6); no npm dependency — `pi` is injected at runtime.
 * Import with `import type` only.
 */

/** A plain JSON Schema object (omp accepts JSON Schema for `parameters`). */
export type JsonSchema = Record<string, unknown>;

export interface TextContent {
  type: "text";
  text: string;
}

export interface ToolResult<D = unknown> {
  content: TextContent[];
  details?: D;
}

export interface SessionEntry {
  type: string;
  [key: string]: unknown;
}

export interface SessionManager {
  getEntries(): SessionEntry[];
  getBranch(): SessionEntry[];
}

export interface ExtensionContext {
  cwd: string;
  sessionManager: SessionManager;
}

export type ToolUpdate = (partial: ToolResult) => void;

export interface ToolDefinition<P = Record<string, unknown>, D = unknown> {
  name: string;
  label: string;
  description: string;
  parameters: JsonSchema;
  hidden?: boolean;
  loadMode?: "essential" | "discoverable";
  /** omp 18.x order: the AbortSignal is the 3rd argument (not the .omp/tools CustomTool order). */
  execute(
    toolCallId: string,
    params: P,
    signal: AbortSignal | undefined,
    onUpdate: ToolUpdate | undefined,
    ctx: ExtensionContext,
  ): Promise<ToolResult<D>>;
}

export interface ExtensionAPI {
  registerTool<P, D>(tool: ToolDefinition<P, D>): void;
  on(event: string, handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
}

export type ExtensionFactory = (pi: ExtensionAPI) => void;
