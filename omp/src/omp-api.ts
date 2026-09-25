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

/** omp `tool_call` event: fired before every tool runs (model loop, eval bridge, xd:// dispatch). Names are canonical (`task`, never `_task`). */
export interface ToolCallEvent {
  toolName: string;
  toolCallId: string;
  input: unknown;
}

/** A `tool_call` handler result: `{block: true, reason}` becomes the error tool result the model sees, verbatim. */
export interface ToolCallResult {
  block?: boolean;
  reason?: string;
}

/** omp `before_agent_start` event: fired once per user prompt, before the agent loop starts. */
export interface BeforeAgentStartEvent {
  prompt: string;
  systemPrompt?: string[];
}

/** A `before_agent_start` result: `systemPrompt` replaces the system prompt parts for this turn. */
export interface BeforeAgentStartResult {
  systemPrompt?: string[];
}

/** omp's extension command context: `hasUI` is false in print/RPC mode, where `ui.notify` is a no-op. */
export interface CommandContext extends ExtensionContext {
  hasUI: boolean;
  ui?: { notify(message: string, level?: "info" | "warning" | "error"): void };
  /** Resolves once the agent stops streaming; a `-p` run exits without it (research P4/P5). */
  waitForIdle(): Promise<void>;
}

/** A `registerCommand` definition: `/name <args>` calls `handler(args, ctx)` in every mode, `-p` included. */
export interface CommandDefinition {
  description: string;
  handler(args: string, ctx: CommandContext): Promise<void>;
}

export interface ExtensionAPI {
  registerTool<P, D>(tool: ToolDefinition<P, D>): void;
  on(event: "tool_call", handler: (event: ToolCallEvent, ctx: ExtensionContext) => ToolCallResult | undefined): void;
  on(
    event: "before_agent_start",
    handler: (event: BeforeAgentStartEvent, ctx: ExtensionContext) => BeforeAgentStartResult | undefined,
  ): void;
  on(event: string, handler: (event: unknown, ctx: ExtensionContext) => unknown): void;
  /** Canonical names of the session's active tools. A load-time stub that throws: call it only inside handlers. */
  getActiveTools(): string[];
  /** Legal at load (registration only). */
  registerCommand(name: string, command: CommandDefinition): void;
  /** Starts a turn when idle, queues a steer while streaming. A load-time stub that throws: call it only inside handlers. */
  sendUserMessage(text: string): void;
}

export type ExtensionFactory = (pi: ExtensionAPI) => void;
