/**
 * Agent Swarm dock: a custom card mounted above the editor, not a transcript toast.
 * Host contract: `ui.setWidget(key, factory, { placement: "aboveEditor" })`.
 * Default placement is already aboveEditor; we set it explicitly so the card
 * cannot fall below the composer.
 */

export const SWARM_DOCK_KEY = "agent-swarm";

export interface SwarmDockRow {
  id: string;
  agent: string;
  status: string;
  detail?: string;
}

export interface SwarmDock {
  title: string;
  rows: SwarmDockRow[];
  footer?: string;
}

export interface SwarmDockUi {
  setWidget?(
    key: string,
    content: ((tui: unknown, theme: SwarmDockTheme) => SwarmDockComponent) | undefined,
    options?: { placement?: "aboveEditor" | "belowEditor" },
  ): void;
}

export interface SwarmDockTheme {
  fg?: (color: string, text: string) => string;
}

export interface SwarmDockComponent {
  render(width: number): readonly string[];
}

/** Dock state owned by one session. omp builds the extension once per session, so each owns one. */
export interface DockState {
  live: SwarmDock;
  /** toolCallId → row ids painted for that `task` call. Settled by `tool_result`. */
  pendingSpawns: Map<string, string[]>;
  /** Numbers `task` calls that arrive without a toolCallId, so their rows still get distinct ids. */
  anonymousCalls: number;
}

export function createDockState(): DockState {
  return { live: { title: "Agent Swarm", rows: [] }, pendingSpawns: new Map(), anonymousCalls: 0 };
}

function paint(theme: SwarmDockTheme | undefined, color: string, text: string): string {
  try {
    return theme?.fg?.(color, text) ?? text;
  } catch {
    return text;
  }
}

function clip(text: string, width: number): string {
  if (width <= 0) return "";
  if (text.length <= width) return text;
  if (width === 1) return "…";
  return `${text.slice(0, width - 1)}…`;
}

function glyph(status: string): { mark: string; color: string } {
  const s = status.toLowerCase();
  if (s === "completed" || s === "done") return { mark: "✓", color: "success" };
  if (s === "failed" || s === "error") return { mark: "✕", color: "error" };
  if (s === "empty") return { mark: "○", color: "warning" };
  if (s === "loading") return { mark: "◌", color: "muted" };
  if (s === "retry" || s === "warning") return { mark: "!", color: "warning" };
  return { mark: "●", color: "accent" };
}

/** Bordered card. Every line is exactly `width` characters before color is applied. */
export function renderSwarmDock(dock: SwarmDock, width: number, theme?: SwarmDockTheme): string[] {
  const w = Math.max(24, width);
  const inner = w - 2;
  const rule = (left: string, right: string, label: string, color: string): string => {
    const text = clip(label, inner);
    const pad = Math.max(0, inner - text.length);
    return paint(theme, color, `${left}${text}${"─".repeat(pad)}${right}`);
  };
  const row = (body: string, color: string): string => {
    return paint(theme, color, `│${clip(body, inner).padEnd(inner)}│`);
  };
  const lines = [rule("╭", "╮", `─ ${dock.title} `, "accent")];
  const shown = dock.rows.slice(-6);
  if (shown.length === 0) lines.push(row(" waiting for subagents", "muted"));
  for (const item of shown) {
    const g = glyph(item.status);
    const detail = item.detail ? `  ${item.detail}` : "";
    lines.push(row(` ${g.mark} ${item.agent}  ${item.status}${detail}`, g.color));
  }
  const liveCount = dock.rows.filter((r) => {
    const s = r.status.toLowerCase();
    return s === "running" || s === "launching" || s === "in_progress" || s === "loading";
  }).length;
  const foot = dock.footer ?? `${dock.rows.length} subagent${dock.rows.length === 1 ? "" : "s"} · ${liveCount} live`;
  lines.push(rule("╰", "╯", `─ ${foot} `, "muted"));
  return lines;
}
export function swarmDockWidget(state: DockState): (tui: unknown, theme: SwarmDockTheme) => SwarmDockComponent {
  // Render from the session's live state, not a snapshot taken at setWidget time. A host that keeps the
  // first component and calls render() again would otherwise stay on the first status.
  return (_tui, theme) => ({
    render(width: number) {
      const { live } = state;
      return renderSwarmDock(
        { title: live.title, footer: live.footer, rows: live.rows.map((r) => ({ ...r })) },
        width,
        theme,
      );
    },
  });
}

export function publishSwarmDock(ui: SwarmDockUi | undefined, state: DockState): void {
  if (!ui?.setWidget) return;
  const { live } = state;
  if (live.rows.length === 0 && !live.footer) {
    ui.setWidget(SWARM_DOCK_KEY, undefined, { placement: "aboveEditor" });
    return;
  }
  ui.setWidget(SWARM_DOCK_KEY, swarmDockWidget(state), { placement: "aboveEditor" });
}

export function upsertDockRow(state: DockState, row: SwarmDockRow): void {
  const rows = state.live.rows;
  const i = rows.findIndex((r) => r.id === row.id);
  if (i >= 0) rows[i] = { ...rows[i], ...row };
  else rows.push(row);
  if (rows.length > 8) rows.splice(0, rows.length - 8);
}

function liveFooter(state: DockState): string {
  const rows = state.live.rows;
  const running = rows.filter((row) => {
    const s = row.status.toLowerCase();
    return s === "running" || s === "launching" || s === "in_progress" || s === "loading";
  }).length;
  const failed = rows.filter((row) => row.status === "failed" || row.status === "error").length;
  if (running > 0) return `${running} running`;
  if (failed > 0) return `${failed} failed`;
  return `${rows.length} done`;
}

/** The `ui` a tool event may carry. Absent when the host has no card surface. */
export function dockUi(ctx: unknown): SwarmDockUi | undefined {
  if (!ctx || typeof ctx !== "object" || !("ui" in ctx)) return undefined;
  const ui = ctx.ui;
  if (!ui || typeof ui !== "object" || !("setWidget" in ui) || typeof ui.setWidget !== "function") return undefined;
  return { setWidget: ui.setWidget.bind(ui) };
}

export function setDockFooter(state: DockState, footer: string): void {
  state.live.footer = footer;
}

export function flushDock(state: DockState, ui: SwarmDockUi | undefined): void {
  publishSwarmDock(ui, state);
}

export function clearDock(state: DockState, ui: SwarmDockUi | undefined): void {
  state.live.rows.length = 0;
  state.live.footer = undefined;
  state.pendingSpawns.clear();
  publishSwarmDock(ui, state);
}

/** Rows from a `task` tool call. Empty when the call is not a subagent spawn. */
export function rowsFromTaskInput(input: unknown, callKey: string): SwarmDockRow[] {
  if (!input || typeof input !== "object" || !("tasks" in input)) return [];
  const tasks: unknown = input.tasks;
  if (!Array.isArray(tasks)) return [];
  const rows: SwarmDockRow[] = [];
  for (let i = 0; i < tasks.length; i++) {
    const item: unknown = tasks[i];
    if (!item || typeof item !== "object") continue;
    const agent = "agent" in item && typeof item.agent === "string" && item.agent.trim() ? item.agent.trim() : "task";
    // The call and the item index keep ids distinct, even when two calls reuse a task name.
    rows.push({ id: `${callKey}#${i}`, agent, status: "running" });
  }
  return rows;
}

export interface TaskResultEvent {
  toolName?: string;
  toolCallId?: string;
  isError?: boolean;
}

export function publishTaskSpawn(state: DockState, ui: SwarmDockUi | undefined, input: unknown, toolCallId?: string): void {
  // Without a toolCallId the call is never settled, but its rows still need their own ids.
  const callKey = toolCallId ?? `anon-${state.anonymousCalls++}`;
  const rows = rowsFromTaskInput(input, callKey);
  if (rows.length === 0) return;
  for (const row of rows) upsertDockRow(state, row);
  if (toolCallId) state.pendingSpawns.set(toolCallId, rows.map((row) => row.id));
  setDockFooter(state, liveFooter(state));
  flushDock(state, ui);
}

/** Move the rows of one accepted `task` call off `running` when the tool returns. */
export function settleTaskResult(state: DockState, ui: SwarmDockUi | undefined, event: TaskResultEvent): void {
  if (event.toolName !== "task" || !event.toolCallId) return;
  const ids = state.pendingSpawns.get(event.toolCallId);
  state.pendingSpawns.delete(event.toolCallId);
  if (!ids || ids.length === 0) return;
  const status = event.isError ? "failed" : "completed";
  for (const id of ids) {
    const existing = state.live.rows.find((row) => row.id === id);
    if (!existing) continue;
    upsertDockRow(state, { id, agent: existing.agent, status });
  }
  setDockFooter(state, liveFooter(state));
  flushDock(state, ui);
}
