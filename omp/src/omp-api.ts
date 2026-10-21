/**
 * Minimal structural types for the omp extension API surface this package uses (D-02).
 * Mirrors @oh-my-pi/pi-coding-agent 18.12.1 for what is used (verified against its source: extensibility/extensions/types.ts,
 * utils/event-bus.ts, task/types.ts, and @oh-my-pi/pi-tui theme, chat/extension-types.ts, overlays/session-observer-registry.ts);
 * no npm dependency — `pi` is injected at runtime. Import with `import type` only.
 *
 * Fields the real 18.12.1 host always sets but 18.3.1 hosts and this package's test fakes omit (`ExtensionContext.ui/mode/hasUI/agent`,
 * `ExtensionAPI.events`) are declared optional here, so every reader must guard them; the swarm bar does.
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
  /** The session's id; read through an optional call because 18.3.1 hosts and test fakes omit it. */
  getSessionId?(): unknown;
}

/** Run mode of the host: `tui` is the only one with terminal components. */
export type ExtensionMode = "tui" | "rpc" | "json" | "print";

/** Identity of the session's agent. `kind: "sub"` is any spawned session (extension factories are rebound to every subagent). */
export interface ExtensionAgentIdentity {
  kind: "main" | "sub";
  id: string;
  name: string;
  depth: number;
  parentId?: string;
}

/** `WidgetPlacement` of 18.12.1: widgets of one placement stack in insertion order; re-setting a key moves it to the bottom. */
export type WidgetPlacement = "aboveEditor" | "belowEditor";

/** The TUI handed to a widget factory: the only member the bar uses. */
export interface WidgetHost {
  requestRender(): void;
}

/** A widget component: `render(width)` must return rows no wider than `width` columns. */
export interface WidgetComponent {
  render(width: number): readonly string[];
  invalidate?(): void;
  dispose?(): void;
}

/** The theme members a widget may read (pi-tui theme-class.ts: `fg` and `getFgAnsi` throw on an unknown color). */
export interface Theme {
  fg(color: string, text: string): string;
  bold(text: string): string;
  getFgAnsi(color: string): string;
  getBgAnsi(color: string): string;
  readonly status: Record<string, string>;
  readonly sep: Record<string, string>;
  getSpinnerFrames(type?: "status" | "activity"): string[];
}

export type WidgetFactory = (tui: WidgetHost, theme: Theme) => WidgetComponent;

/** The `ctx.ui` members used here. `string[]` widgets are capped at 10 lines and are RPC-compatible; factories are TUI-only. */
export interface ExtensionUIContext {
  notify(message: string, level?: "info" | "warning" | "error"): void;
  /** Footer status text keyed per extension; `undefined` clears it. Rendered key-sorted inside the shared status segment. */
  setStatus?(key: string, text: string | undefined): void;
  /** Custom card above (`aboveEditor`) or below the composer. Not a transcript toast. `undefined` removes the key. */
  setWidget?(key: string, content: string[] | WidgetFactory | undefined, options?: { placement?: WidgetPlacement }): void;
}

/** omp's shared event bus (utils/event-bus.ts): handlers run synchronously on `emit`; `on` returns an unsubscribe. */
export interface EventBus {
  emit(channel: string, data: unknown): void;
  on(channel: string, handler: (data: unknown) => void): () => void;
}

/** `AgentProgress` (pi-tui tools/task.ts), the fields the bar reads. */
export interface AgentProgress {
  index: number;
  id: string;
  agent: string;
  status: "pending" | "running" | "completed" | "failed" | "aborted";
  task: string;
  assignment?: string;
  description?: string;
  lastIntent?: string;
  currentTool?: string;
  currentToolArgs?: string;
  currentToolIntent?: string;
  currentToolStartMs?: number;
  toolCount: number;
  requests: number;
  tokens: number;
  cost: number;
  durationMs: number;
  retryState?: { attempt: number; maxAttempts: number; delayMs: number; errorMessage: string; startedAtMs: number };
  retryFailure?: { attempt: number; errorMessage: string };
  /**
   * The latest `TaskToolDetails` of the subagent's own in-flight `task` call (omp's executor copies each `tool_execution_update`
   * into it and clears it when that call ends). A subagent's session has no parent event bus, so this is the only way a specialist
   * an orchestrator (A01) spawns in-session reaches the top-level session.
   */
  inflightTaskDetails?: TaskToolDetails;
}

/** `SingleResult` (pi-tui tools/task.ts), the fields the bar reads. */
export interface SingleResult {
  index: number;
  id: string;
  agent: string;
  task: string;
  assignment?: string;
  description?: string;
  exitCode: number;
  durationMs: number;
  tokens: number;
  error?: string;
  aborted?: boolean;
  retryFailure?: { attempt: number; errorMessage: string };
}

/** `TaskToolDetails` (pi-tui tools/task.ts): one `task` call's settled `results` and, while it runs, a `progress` entry per spawn. */
export interface TaskToolDetails {
  results: SingleResult[];
  totalDurationMs: number;
  progress?: AgentProgress[];
}

/** Bus channel `task:subagent:progress` (coalesced to about 150 ms per agent). */
export interface SubagentProgressPayload {
  index: number;
  agent: string;
  task: string;
  parentToolCallId?: string;
  assignment?: string;
  progress: AgentProgress;
  sessionFile?: string;
  detached?: boolean;
}

/** Bus channel `task:subagent:lifecycle`. */
export interface SubagentLifecyclePayload {
  id: string;
  agent: string;
  description?: string;
  status: "started" | "completed" | "failed" | "aborted";
  sessionFile?: string;
  parentToolCallId?: string;
  index: number;
  detached?: boolean;
}

export interface ExtensionContext {
  cwd: string;
  sessionManager: SessionManager;
  /** UI methods; absent in test fakes. In print/RPC mode `hasUI` is false and widgets are unavailable. */
  ui?: ExtensionUIContext;
  mode?: ExtensionMode;
  hasUI?: boolean;
  /** Identity of this session's agent (18.3.2+). */
  agent?: ExtensionAgentIdentity;
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
  /**
   * Resolves once the agent loop is not running. During a prompt's pre-loop window (right after `sendUserMessage`)
   * it resolves at once (G-04-05-1), so it is a post-turn drain, not the turn signal; `/swarm` holds on `isIdle`.
   */
  waitForIdle(): Promise<void>;
  /** False for the whole prompt, from before the agent loop until after it (omp: `!session.isStreaming`; 18.0.6 and 18.3.1): the turn signal. */
  isIdle(): boolean;
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
  /** Shared event bus (`task:subagent:progress`, `task:subagent:lifecycle`). Read it inside handlers only. */
  events?: EventBus;
  /** Canonical names of the session's active tools. A load-time stub that throws: call it only inside handlers. */
  getActiveTools(): string[];
  /** Legal at load (registration only). */
  registerCommand(name: string, command: CommandDefinition): void;
  /** Starts a turn when idle, queues a steer while streaming. A load-time stub that throws: call it only inside handlers. */
  sendUserMessage(text: string): void;
}

export type ExtensionFactory = (pi: ExtensionAPI) => void;
